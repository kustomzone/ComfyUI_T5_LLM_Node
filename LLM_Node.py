from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    AutoModelForCausalLM,
    AutoModelForSeq2SeqLM,
    AutoConfig,
    BitsAndBytesConfig
)
from llama_cpp import Llama
import torch
import os
import folder_paths
import re
import subprocess

GLOBAL_MODELS_DIR = os.path.join(folder_paths.models_dir, "LLM_checkpoints")

WEB_DIRECTORY = "./web/assets/js"

class AnyType(str):
  """A special class that is always equal in not equal comparisons. Credit to pythongosssss"""

  def __ne__(self, __value: object) -> bool:
    return False

any = AnyType("*")

class LLM_Node:
    def __init__(self, device="cuda"):
        self.device = device
        # Check if bfloat16 is supported by the device
        self.supports_bfloat16 = 'cuda' in device and torch.cuda.is_bf16_supported()

    @classmethod
    def INPUT_TYPES(cls):
        # Get a list of directories in the checkpoints_path
        model_options = []
        for name in os.listdir(GLOBAL_MODELS_DIR):
            dir_path = os.path.join(GLOBAL_MODELS_DIR, name)
            if os.path.isdir(dir_path):
                if "GGUF" in name:
                    gguf_files = [os.path.join(name, file) for file in os.listdir(dir_path) if file.endswith('.gguf')]
                    model_options.extend(gguf_files)
                else:
                    model_options.append(name)

        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "seed": ("INT", {"default": 777}),
                "model": (model_options, ),
                "max_tokens": ("INT", {"default": 2000, "min": 1}),
                "apply_chat_template": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "AdvOptionsConfig": ("ADVOPTIONSCONFIG",),
                "QuantizationConfig": ("QUANTIZATIONCONFIG",),
                "CodingConfig": ("CODINGCONFIG",),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("string",)
    OUTPUT_NODE = False
    FUNCTION = "main"
    CATEGORY = "LLM"

    def generate_text(self, text, tokenizer, model_to_use, generate_kwargs, apply_chat_template):
        if apply_chat_template:
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": text}
            ]
            text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )

        input_ids = tokenizer([text], return_tensors="pt").input_ids.to(self.device)
        outputs = model_to_use.generate(input_ids, **generate_kwargs)

        if apply_chat_template:
            generated_ids = [output_ids[len(input_id):] for input_id, output_ids in zip(input_ids, outputs)]
            generated_text = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        else:
            generated_text = tokenizer.decode(outputs[0], skip_special_tokens=True)

        return generated_text

    def main(self, text, seed, model, max_tokens, apply_chat_template, AdvOptionsConfig=None, QuantizationConfig=None, CodingConfig=None):
        model_path = os.path.join(GLOBAL_MODELS_DIR, model)
        generated_text = None
        if "GGUF" in model:
            generate_kwargs = {'max_tokens': max_tokens}

            if AdvOptionsConfig:
                for option in ['temperature', 'top_p', 'top_k', 'repetition_penalty']:
                    if option in AdvOptionsConfig:
                        if (option == 'repetition_penalty'):
                            option1 = 'repeat_penalty'
                        else:
                            option1 = option
                        generate_kwargs[option1] = AdvOptionsConfig[option]


            model_to_use = Llama(
                model_path=model_path,
                n_gpu_layers=-1,
                seed=seed,
                # n_ctx=2048, # Uncomment to increase the context window
            )
            generated_text = model_to_use(text, **generate_kwargs)
            return (generated_text['choices'][0]['text'],)
        else:
            torch.manual_seed(seed)

            model_kwargs = {
                'device_map': 'auto',
                'quantization_config': QuantizationConfig
            }

            if AdvOptionsConfig:
                if 'trust_remote_code' in AdvOptionsConfig:
                    model_kwargs['trust_remote_code'] = AdvOptionsConfig['trust_remote_code']
                
                if 'torch_dtype' in AdvOptionsConfig and hasattr(torch, AdvOptionsConfig['torch_dtype']):
                    model_kwargs['torch_dtype'] = getattr(torch, AdvOptionsConfig['torch_dtype'])

            config = AutoConfig.from_pretrained(model_path, **model_kwargs)
            tokenizer = AutoTokenizer.from_pretrained(model_path)
 
            if config.model_type == "t5":
                model_to_use = AutoModelForSeq2SeqLM.from_pretrained(model_path, **model_kwargs)
            elif config.model_type in ["gpt2", "gpt_refact", "gemma", "llama", "mistral", "qwen2"]:
                model_to_use = AutoModelForCausalLM.from_pretrained(model_path, **model_kwargs)
            elif config.model_type == "bert":
                model_to_use = AutoModelForSequenceClassification.from_pretrained(model_path, **model_kwargs)
            else:
                raise ValueError(f"Unsupported model type: {config.model_type}")

            generate_kwargs = {'max_length': max_tokens}

            if AdvOptionsConfig:
                for option in ['temperature', 'top_p', 'top_k', 'repetition_penalty']:
                    if option in AdvOptionsConfig:
                        generate_kwargs[option] = AdvOptionsConfig[option]

            if config.model_type in ["t5", "gpt2", "gpt_refact", "gemma", "llama", "mistral", "qwen2"]:
                generated_text = self.generate_text(text, tokenizer, model_to_use, generate_kwargs, apply_chat_template)
                
                if CodingConfig and CodingConfig.get('execute_code'):
                    execution_attempts = 0
                    while True:
                        pattern = r'```python\s(.*?)```'
                        code = re.search(pattern, generated_text, re.DOTALL)
                        if code:
                            extracted_code = code.group(1).strip()
                            try:
                                command = ['python', '-c', extracted_code]
                                result = subprocess.run(command, capture_output=True, text=True, check=True)
                                print("Output:", result.stdout)
                                print("Successful Execution. Exiting loop.")
                                break
                            except subprocess.CalledProcessError as e:
                                print(f"Execution failed, retrying... Error: {e.stderr}")
                                text = f"Error encountered: {e.stderr}\nFix the code. Write whole code.\n{extracted_code}"
                                generated_text = self.generate_text(text, tokenizer, model_to_use, generate_kwargs, apply_chat_template)
                                continue
                        execution_attempts += 1
                        if execution_attempts > 10:
                            print("Maximum execution attempts reached, exiting.")
                            break
                    return (generated_text,)
                else:
                    return (generated_text,)
            elif config.model_type == "bert":
                return ("BERT model detected; specific task handling not implemented in this example.",)

class Output_Node:
    def __init__(self):
        pass
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (any, {}),
            }
        }
    
    OUTPUT_NODE = True
    FUNCTION = "main"
    CATEGORY = "LLM"
    RETURN_TYPES = ()

    def main(self, text):
        return {"ui": {"text": (text,)}}

class QuantizationConfig_Node:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        quantization_modes = ["none", "load_in_8bit", "load_in_4bit"]
        return {
            "required": {
                "quantization_mode": (quantization_modes, {"default": "none"}),
                "llm_int8_threshold": ("FLOAT", {"default": 6.0}),
                "llm_int8_skip_modules": ("STRING", {"default": ""}),
                "llm_int8_enable_fp32_cpu_offload": ("BOOLEAN", {"default": False}),
                "llm_int8_has_fp16_weight": ("BOOLEAN", {"default": False}),
                "bnb_4bit_compute_dtype": ("STRING", {"default": "float32"}),
                "bnb_4bit_quant_type": ("STRING", {"default": "fp4"}),
                "bnb_4bit_use_double_quant": ("BOOLEAN", {"default": False}),
                "bnb_4bit_quant_storage": ("STRING", {"default": "uint8"}),
            }
        }
    
    FUNCTION = "main"
    CATEGORY = "LLM"
    RETURN_TYPES = ("QUANTIZATIONCONFIG",)
    RETURN_NAMES = ("QuantizationConfig",)

    def main(self, quantization_mode, llm_int8_threshold: float = 6.0, llm_int8_skip_modules="", llm_int8_enable_fp32_cpu_offload=False, llm_int8_has_fp16_weight=False, bnb_4bit_compute_dtype="float32", bnb_4bit_quant_type="fp4", bnb_4bit_use_double_quant=False, bnb_4bit_quant_storage="uint8"):

        llm_int8_skip_modules_list = llm_int8_skip_modules.split(',') if llm_int8_skip_modules else []

        quantization_config = BitsAndBytesConfig(
            load_in_8bit=quantization_mode == "load_in_8bit",
            load_in_4bit=quantization_mode == "load_in_4bit",
            llm_int8_threshold=float(llm_int8_threshold),
            llm_int8_skip_modules=llm_int8_skip_modules_list,
            llm_int8_enable_fp32_cpu_offload=llm_int8_enable_fp32_cpu_offload,
            llm_int8_has_fp16_weight=llm_int8_has_fp16_weight,
            bnb_4bit_compute_dtype=getattr(torch, bnb_4bit_compute_dtype, torch.float32),
            bnb_4bit_quant_type=bnb_4bit_quant_type,
            bnb_4bit_use_double_quant=bnb_4bit_use_double_quant,
            bnb_4bit_quant_storage=bnb_4bit_quant_storage,
        )

        return (quantization_config,)

class AdvOptionsNode:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        dtype_options = ["auto", "float32", "bfloat16", "float16", "float64"]
        return {
            "required": {
                "temperature": ("FLOAT", {"default": 1.0, "min": 0.1, "step": 0.1}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.1, "step": 0.1}),
                "top_k": ("INT", {"default": 50, "min": 0}),
                "repetition_penalty": ("FLOAT", {"default": 1.2, "min": 0.1, "step": 0.1}),
                "trust_remote_code": ("BOOLEAN", {"default": False}),
                "torch_dtype": (dtype_options, {"default": "auto"}),
            }
        }

    FUNCTION = "main"
    CATEGORY = "LLM"
    RETURN_TYPES = ("ADVOPTIONSCONFIG",)
    RETURN_NAMES = ("AdvOptionsConfig",)

    def main(self, temperature=1.0, top_p=0.9, top_k=50, repetition_penalty=1.2, trust_remote_code=False, torch_dtype="auto"):
        options_config = {
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repetition_penalty": repetition_penalty,
            "trust_remote_code": trust_remote_code,
            "torch_dtype": torch_dtype,
        }

        return (options_config,)

class CodingOptionsNode:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "execute_code": ("BOOLEAN", {"default": False}),
            }
        }

    FUNCTION = "main"
    CATEGORY = "LLM"
    RETURN_TYPES = ("CODINGCONFIG",)
    RETURN_NAMES = ("CodingConfig",)

    def main(self, execute_code):

        return ({"execute_code":execute_code,},)
    
NODE_CLASS_MAPPINGS = {
    "LLM_Node": LLM_Node,
    "Output_Node": Output_Node,
    "QuantizationConfig_Node": QuantizationConfig_Node,
    "AdvOptions_Node": AdvOptionsNode,
    "CodingOptionsNode": CodingOptionsNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLM_Node": "LLM Node",
    "Output_Node": "Output Node",
    "QuantizationConfig_Node": "Quantization Config Node",
    "AdvOptions_Node": "Advanced Options Node",
    "CodingOptionsNode": "Code Config Node",
}

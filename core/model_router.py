import os
from openai import OpenAI
from ollama import Client

class ModelRouter:
    def __init__(self):
        self.local_client = Client(host=os.getenv("OLLAMA_HOST"))
        self.model = os.getenv("MODEL", "qwen3.5:4b")
        self.api_enabled = os.getenv("API_FALLBACK_ENABLED", "true").lower() == "true"
        self.openrouter = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY")
        )

    def chat(self, messages: list, temperature: float = 0.3):
        if self.api_enabled:
            response = self.openrouter.chat.completions.create(
                model="anthropic/claude-3.5-sonnet",
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content
        else:
            return self.local_client.chat(
                model=self.model,
                messages=messages,
                format="json"
            ).message.content
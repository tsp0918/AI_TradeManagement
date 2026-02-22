import ollama
from typing import List, Dict, Optional
from app.config import settings


class OllamaService:
    """Service for extracting use cases from patents using local LLM."""

    def __init__(self, model: Optional[str] = None):
        """
        Initialize Ollama service.

        Args:
            model: Ollama model to use (defaults to settings.ollama_model)
        """
        self.model = model or settings.ollama_model
        self.host = settings.ollama_host
        self.timeout = settings.ollama_timeout

        # Initialize client with custom host
        self.client = ollama.Client(host=self.host)

    def _build_prompt(
        self,
        title: str,
        abstract: str,
        description: Optional[str] = "",
        claims: Optional[str] = ""
    ) -> str:
        """Build prompt for use case extraction in Japanese."""

        prompt = f"""あなたは特許分析の専門家です。以下の特許を分析し、3〜5つの実用的な用途や応用例を抽出してください。

特許タイトル: {title}

要約: {abstract}
"""

        if description:
            # Limit description length to avoid token overflow
            desc_preview = description[:2000] + "..." if len(description) > 2000 else description
            prompt += f"\n詳細説明: {desc_preview}\n"

        if claims:
            # Limit claims length to avoid token overflow
            claims_preview = claims[:1500] + "..." if len(claims) > 1500 else claims
            prompt += f"\nクレーム: {claims_preview}\n"

        prompt += """
各用途について、以下の情報を提供してください：
1. 明確な説明（1〜2文）
2. 適用される業界や分野
3. 主な利点や優位性

番号付きリストで回答してください。具体的で実用的な内容にしてください。

回答形式の例:
1. [用途名]
   説明: [1〜2文の説明]
   業界: [業界・分野]
   利点: [主な優位性]

2. [用途名]
   説明: [1〜2文の説明]
   業界: [業界・分野]
   利点: [主な優位性]

それでは、上記の特許を分析してください（必ず日本語で回答してください）:
"""

        return prompt

    def _parse_use_cases(self, response_text: str) -> List[str]:
        """
        Parse LLM response to extract individual use cases.

        Args:
            response_text: Raw response from LLM

        Returns:
            List of use case strings
        """
        # Split by numbered items
        use_cases = []
        lines = response_text.strip().split('\n')

        current_use_case = []
        for line in lines:
            line = line.strip()

            # Check if line starts with a number (new use case)
            if line and (
                line[0].isdigit() or
                (len(line) > 2 and line[0:2].replace('.', '').isdigit())
            ):
                # Save previous use case if exists
                if current_use_case:
                    use_cases.append('\n'.join(current_use_case))
                    current_use_case = []

            if line:
                current_use_case.append(line)

        # Add the last use case
        if current_use_case:
            use_cases.append('\n'.join(current_use_case))

        return use_cases

    async def extract_use_cases(
        self,
        title: str,
        abstract: str,
        description: Optional[str] = "",
        claims: Optional[str] = ""
    ) -> Dict[str, any]:
        """
        Extract use cases from patent using local LLM.

        Args:
            title: Patent title
            abstract: Patent abstract
            description: Patent description (optional)
            claims: Patent claims (optional)

        Returns:
            Dict with 'use_cases' (list), 'raw_response' (str), and 'model' (str)
        """
        try:
            # Build prompt
            prompt = self._build_prompt(title, abstract, description, claims)

            # Call Ollama
            response = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "あなたは特許文書から実用的な応用例や用途を特定することを専門とする特許分析の専門家です。必ず日本語で回答してください。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                options={
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            )

            # Extract response text
            response_text = response['message']['content']

            # Parse use cases
            use_cases = self._parse_use_cases(response_text)

            return {
                "use_cases": use_cases,
                "raw_response": response_text,
                "model": self.model,
                "success": True
            }

        except Exception as e:
            return {
                "use_cases": [],
                "raw_response": "",
                "model": self.model,
                "success": False,
                "error": str(e)
            }

    async def extract_use_cases_streaming(
        self,
        title: str,
        abstract: str,
        description: Optional[str] = "",
        claims: Optional[str] = ""
    ):
        """
        Extract use cases with streaming response (for real-time UI updates).

        Args:
            title: Patent title
            abstract: Patent abstract
            description: Patent description (optional)
            claims: Patent claims (optional)

        Yields:
            Chunks of text as they're generated
        """
        try:
            prompt = self._build_prompt(title, abstract, description, claims)

            stream = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "あなたは特許文書から実用的な応用例や用途を特定することを専門とする特許分析の専門家です。必ず日本語で回答してください。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                stream=True,
                options={
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            )

            for chunk in stream:
                if 'message' in chunk and 'content' in chunk['message']:
                    yield chunk['message']['content']

        except Exception as e:
            yield f"Error: {str(e)}"

    async def check_connection(self) -> bool:
        """Check if Ollama service is available."""
        try:
            # Try to list models to check connection
            self.client.list()
            return True
        except Exception as e:
            print(f"Ollama connection check failed: {e}")
            return False

    async def list_available_models(self) -> List[str]:
        """List available Ollama models."""
        try:
            models = self.client.list()
            return [model['name'] for model in models.get('models', [])]
        except Exception:
            return []

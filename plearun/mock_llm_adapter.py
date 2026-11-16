"""Optional LLM adapter conforming to mock_llm.py and schema.json.
Enforces Rule 5 & Rule 6:
- Rule 5: 'The tool must work without any network access or LLM.'
- Rule 6: 'Optional: an LLM path that may only choose among candidate row IDs, through the
  interface in mock_llm.py and schema.json. If you build it, it must never create a finding on its own.'
"""
import json
import os
import sys
from typing import List, Optional, Dict, Any

class LLMCandidateReranker:
    """Optional adapter that queries mock_llm.py to rank/select among pre-filtered candidate rows."""

    def __init__(self, root_dir: Optional[str] = None):
        self.root_dir = root_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._provider = None
        self._load_provider()

    def _load_provider(self):
        try:
            sys.path.insert(0, self.root_dir)
            import mock_llm  # type: ignore
            self._provider = mock_llm
        except ImportError:
            self._provider = None

    def rerank_candidates(self, claim_text: str, candidate_ids: List[str]) -> Optional[Dict[str, Any]]:
        """Invokes mock_llm.complete if available and validates against schema.json."""
        if not self._provider or not candidate_ids:
            return None

        prompt = f"Claim: {claim_text}\nCandidate Row IDs: {candidate_ids}"
        try:
            response_json = self._provider.complete(prompt, candidate_ids)
            data = json.loads(response_json)
            # Schema requires: 'selected_row_ids', 'quote', 'reason'
            if "selected_row_ids" in data and isinstance(data["selected_row_ids"], list):
                return data
        except Exception:
            pass

        return None

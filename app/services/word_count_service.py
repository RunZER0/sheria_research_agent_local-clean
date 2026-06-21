import re
from app.schemas.document_spec import DocumentSpec


class WordCountService:
    @staticmethod
    def count_words(text: str) -> int:
        """Standardized counting rule for ordinary words, numbers, and contractions."""
        if not text:
            return 0
        return len(re.findall(r"\b[\w]+(?:[''\-][\w]+)?\b", text))

    @classmethod
    def verify_spec(cls, spec: DocumentSpec) -> int:
        """Extracts text strictly within the defined scope of the constraints."""
        scope = spec.constraints.word_count.scope
        text_buffer = []

        for block in spec.blocks:
            if scope == "body_only" and block.type in ["table", "heading"]:
                continue
            if scope == "exclude_tables" and block.type == "table":
                continue

            if block.type == "paragraph":
                text_buffer.append(block.text)
            elif block.type == "heading":
                text_buffer.append(block.text)
            elif block.type == "bullet_list":
                text_buffer.extend(block.items)

        combined_text = " ".join(text_buffer)
        return cls.count_words(combined_text)

"""Validators for SCT items."""

import re
from typing import List

from ..logging import get_logger
from ..schemas.sct_schemas import SCTItem
from ..schemas.validator_schemas import ValidationResult

logger = get_logger(__name__)


class SCTValidator:
    """Validator for SCT (Script Concordance Test) items."""

    # Valid options
    VALID_OPTIONS = ["+2", "+1", "0", "-1", "-2"]

    # Valid domains (regex pattern - allow spaces and common punctuation for disease names)
    DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9_ \-()]{3,60}$")

    def __init__(self):
        """Initialize the validator."""
        logger.debug("SCT Validator initialized")

    def validate(self, item: SCTItem) -> ValidationResult:
        """
        Validate a complete SCT item with 3 evaluation scenarios.

        Args:
            item: SCTItem object to validate.

        Returns:
            ValidationResult with errors and warnings.
        """
        result = ValidationResult(is_valid=True)

        logger.info(f"Validating SCT item - domain: {item.domain}")

        # Validate top-level fields
        self._validate_domain(item.domain, result)
        self._validate_vignette(item.vignette, result)
        if item.author_notes:
            self._validate_item_author_notes(item.author_notes, result)

        # Validate questions array
        self._validate_questions_array(item.questions, result)

        # Validate each question
        for i, question in enumerate(item.questions, 1):
            logger.debug(
                f"Validating question {i}/{len(item.questions)} ({question.question_type})"
            )
            self._validate_question_item(question, i, item.vignette, result)

        # Log results
        if result.is_valid:
            logger.info(f"✓ Validation passed (warnings: {len(result.warnings)})")
        else:
            logger.error(f"✗ Validation failed with {len(result.errors)} error(s)")

        for error in result.errors:
            logger.error(f"  ERROR: {error}")
        for warning in result.warnings:
            logger.warning(f"  WARNING: {warning}")

        return result

    def _validate_questions_array(self, questions, result: ValidationResult):
        """Validate the questions array structure."""
        if not questions:
            result.add_error("Questions array is required and cannot be empty")
            return

        if len(questions) != 3:
            result.add_error(
                f"Must have exactly 3 questions (diagnosis, management, followup). Got {len(questions)}"
            )
            return

        # Check question types and order
        expected_types = ["diagnosis", "management", "followup"]
        actual_types = [q.question_type for q in questions]

        if actual_types != expected_types:
            result.add_error(
                f"Questions must be in order: diagnosis, management, followup. Got: {actual_types}"
            )

    def _validate_question_item(
        self, question, question_num: int, vignette: str, result: ValidationResult
    ):
        """Validate a single question within the SCT item."""
        prefix = f"Question {question_num} ({question.question_type})"

        # Validate hypothesis
        self._validate_hypothesis_in_question(question.hypothesis, prefix, result)

        # Validate new_information
        self._validate_new_information_in_question(
            question.new_information, vignette, prefix, result
        )

        # Validate options
        self._validate_options_in_question(question.options, prefix, result)

        # Validate author_notes
        self._validate_author_notes_in_question(question.author_notes, prefix, result)

    def _validate_domain(self, domain: str, result: ValidationResult):
        """Validate domain field."""
        if not domain or not domain.strip():
            result.add_error("Domain is required and cannot be empty")
            return

        # Check format
        if not self.DOMAIN_PATTERN.match(domain):
            result.add_error(
                f"Domain '{domain}' format invalid. Must match: ^[A-Za-z0-9_]{{3,40}}$"
            )

        # Check length
        if len(domain) < 3 or len(domain) > 40:
            result.add_error(
                f"Domain length must be between 3-40 characters (got {len(domain)})"
            )

    def _validate_vignette(self, vignette: str, result: ValidationResult):
        """Validate vignette field."""
        if not vignette or not vignette.strip():
            result.add_error("Vignette is required and cannot be empty")
            return

        # Word count - updated to 120-240 words
        word_count = len(vignette.split())
        if word_count < 120:
            result.add_error(f"Vignette too short: {word_count} words (minimum: 120)")
        elif word_count > 240:
            result.add_error(f"Vignette too long: {word_count} words (maximum: 240)")

        # Check for single paragraph (no line breaks)
        if "\n" in vignette.strip():
            result.add_error("Vignette must be a single paragraph (no line breaks)")

    def _validate_hypothesis_in_question(
        self, hypothesis: str, prefix: str, result: ValidationResult
    ):
        """Validate hypothesis field in a question."""
        if not hypothesis or not hypothesis.strip():
            result.add_error(f"{prefix}: Hypothesis is required and cannot be empty")
            return

        # Word count
        word_count = len(hypothesis.split())
        if word_count < 8:
            result.add_error(
                f"{prefix}: Hypothesis too short: {word_count} words (minimum: 8)"
            )
        elif word_count > 25:
            result.add_error(
                f"{prefix}: Hypothesis too long: {word_count} words (maximum: 25)"
            )

        # Check for question format
        if hypothesis.strip().endswith("?"):
            result.add_error(
                f"{prefix}: Hypothesis must be an affirmative statement, not a question"
            )

    def _validate_new_information_in_question(
        self, new_info: str, vignette: str, prefix: str, result: ValidationResult
    ):
        """Validate new_information field in a question."""
        if not new_info or not new_info.strip():
            result.add_error(
                f"{prefix}: New information is required and cannot be empty"
            )
            return

        # Word count
        word_count = len(new_info.split())
        if word_count < 8:
            result.add_error(
                f"{prefix}: New information too short: {word_count} words (minimum: 8)"
            )
        elif word_count > 30:
            result.add_error(
                f"{prefix}: New information too long: {word_count} words (maximum: 30)"
            )

        # Check for question format
        if "?" in new_info:
            result.add_error(
                f"{prefix}: New information must be declarative, not a question"
            )

        # Check for multiple sentences (max 2)
        sentence_count = len([s for s in re.split(r"[.!?]+", new_info) if s.strip()])
        if sentence_count > 2:
            result.add_error(
                f"{prefix}: New information has {sentence_count} sentences (maximum: 2)"
            )

    def _validate_options_in_question(
        self, options: List[str], prefix: str, result: ValidationResult
    ):
        """Validate options field in a question."""
        if not options:
            result.add_error(f"{prefix}: Options are required and cannot be empty")
            return

        # Check exact length
        if len(options) != 5:
            result.add_error(
                f"{prefix}: Options must have exactly 5 elements (got {len(options)})"
            )
            return

        # Check exact values and order
        if options != self.VALID_OPTIONS:
            result.add_error(
                f"{prefix}: Options must be exactly {self.VALID_OPTIONS} in that order (got {options})"
            )

        # Check for duplicates
        if len(options) != len(set(options)):
            result.add_error(f"{prefix}: Options contain duplicates")

        # Check for extra spaces or embedded descriptors
        for i, opt in enumerate(options):
            if opt != opt.strip():
                result.add_error(f"{prefix}: Option {i} has extra spaces: '{opt}'")
            if len(opt) > 2:  # "+2" is 2 chars
                result.add_error(
                    f"{prefix}: Option {i} has embedded descriptor: '{opt}' (should be just '+2', '+1', etc.)"
                )

    def _validate_author_notes_in_question(
        self, author_notes: str, prefix: str, result: ValidationResult
    ):
        """Validate author_notes field in a question."""
        if not author_notes or not author_notes.strip():
            result.add_error(f"{prefix}: Author notes is required and cannot be empty")
            return

        # Check length
        if len(author_notes) > 300:
            result.add_error(
                f"{prefix}: Author notes too long: {len(author_notes)} characters (maximum: 300)"
            )

        # Check sentence count
        sentence_count = len(
            [s for s in re.split(r"[.!?]+", author_notes) if s.strip()]
        )
        if sentence_count > 3:
            result.add_error(
                f"{prefix}: Author notes has {sentence_count} sentences (maximum: 3)"
            )

        # Check that it ends with expected scale value in parentheses
        # Valid formats: (+2), (+1), (0), (-1), (-2)
        scale_pattern = re.compile(r'\([+-][12]|\(0\)')
        author_notes_stripped = author_notes.strip()
        if not scale_pattern.search(author_notes_stripped) or not author_notes_stripped.endswith(('(+2)', '(+1)', '(0)', '(-1)', '(-2)')):
            result.add_error(
                f"{prefix}: Author notes must end with expected scale value in parentheses, e.g., '(+2)', '(-1)', '(0)'"
            )

    def _validate_item_author_notes(
        self, author_notes: str, result: ValidationResult
    ):
        """Validate author_notes field at item level (optional)."""
        if not author_notes or not author_notes.strip():
            return

        # Check length
        if len(author_notes) > 500:
            result.add_error(
                f"Item-level author notes too long: {len(author_notes)} characters (maximum: 500)"
            )


def validate_sct_item(item: SCTItem) -> ValidationResult:
    """
    Convenience function to validate a single SCT item.

    Args:
        item: SCTItem object to validate.

    Returns:
        ValidationResult with errors and warnings.
    """
    validator = SCTValidator()
    return validator.validate(item)

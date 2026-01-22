"""Utility functions for exporting SCT items to different formats."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List

from ..logging import get_logger
from ..schemas import SCTItem

logger = get_logger(__name__)


def export_to_csv(output_path: Path, items: List[SCTItem]) -> None:
    """
    Export SCT items to CSV format with hierarchical structure.
    
    The vignette appears only once per case, with questions below it.
    This reduces redundancy and makes the CSV more readable.

    Args:
        output_path: Path where the CSV file should be saved.
        items: List of SCTItem objects to export.
    """
    if not items:
        logger.warning("No items to export to CSV")
        return

    logger.info(f"Exporting {len(items)} items to CSV: {output_path}")

    # Define CSV columns
    fieldnames = [
        "domain",
        "guideline",
        "vignette",
        "item_author_notes",
        "validator_guideline",
        "validator_notes",
        "question_type",
        "hypothesis",
        "new_information",
        "effect_phrase",
        "options",  # Will be joined as string
        "author_notes",
        "validator_selected_option",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for item in items:
            # Each item has 3 questions
            # First question includes all case information
            # Subsequent questions leave domain/guideline/vignette empty for hierarchy
            for idx, question in enumerate(item.questions):
                # Convert options list to string
                options_str = ", ".join(question.options)
                
                # Get validator response for this question if available
                validator_option = ""
                if item.validator_result:
                    for validator_response in item.validator_result.validator_responses:
                        if validator_response.question_type == question.question_type:
                            validator_option = validator_response.selected_option
                            break

                # First question (idx=0): include full case info
                # Subsequent questions: leave case fields empty for hierarchical structure
                if idx == 0:
                    row = {
                        "domain": item.domain,
                        "guideline": item.guideline or "",
                        "vignette": item.vignette,
                        "item_author_notes": item.author_notes or "",
                        "validator_guideline": item.validator_result.validator_guideline if item.validator_result else "",
                        "validator_notes": item.validator_result.validator_notes if item.validator_result else "",
                        "question_type": question.question_type,
                        "hypothesis": question.hypothesis,
                        "new_information": question.new_information,
                        "effect_phrase": question.effect_phrase,
                        "options": options_str,
                        "author_notes": question.author_notes,
                        "validator_selected_option": validator_option,
                    }
                else:
                    row = {
                        "domain": "",
                        "guideline": "",
                        "vignette": "",
                        "item_author_notes": "",
                        "validator_guideline": "",
                        "validator_notes": "",
                        "question_type": question.question_type,
                        "hypothesis": question.hypothesis,
                        "new_information": question.new_information,
                        "effect_phrase": question.effect_phrase,
                        "options": options_str,
                        "author_notes": question.author_notes,
                        "validator_selected_option": validator_option,
                    }
                writer.writerow(row)

    logger.info(f"✓ CSV export complete: {output_path}")
    logger.info(f"  Format: Hierarchical (vignette shown once per case, {len(items)} cases total)")


def load_all_generated_items(validated_dir: Path) -> List[SCTItem]:
    """
    Load all JSON files from the validated directory.

    Args:
        validated_dir: Path to the directory containing validated JSON files.

    Returns:
        List of SCTItem objects loaded from JSON files.
    """
    if not validated_dir.exists():
        logger.warning(f"Validated directory does not exist: {validated_dir}")
        return []

    json_files = list(validated_dir.glob("sct_*.json"))
    logger.info(f"Found {len(json_files)} JSON files in {validated_dir}")

    items = []
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Remove validation metadata if present
            if "_validation" in data:
                del data["_validation"]

            item = SCTItem(**data)
            items.append(item)

        except Exception as e:
            logger.error(f"Failed to load {json_file.name}: {e}")
            continue

    logger.info(f"Successfully loaded {len(items)} items from JSON files")
    return items


def export_generated_items_to_csv(
    validated_dir: Path, output_dir: Path, filename: str = None
) -> Path:
    """
    Load all validated items and export them to a CSV file.

    Args:
        validated_dir: Path to directory with validated JSON files.
        output_dir: Path to directory where CSV should be saved.
        filename: Optional custom filename. If None, generates timestamped name.

    Returns:
        Path to the created CSV file.
    """
    # Load all items from validated folder
    items = load_all_generated_items(validated_dir)

    if not items:
        logger.warning("No items found to export")
        return None

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename if not provided
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sct_items_{timestamp}.csv"

    output_path = output_dir / filename

    # Export to CSV
    export_to_csv(output_path, items)

    return output_path


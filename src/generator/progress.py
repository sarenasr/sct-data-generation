"""Progress tracking and graceful shutdown for SCT generation."""

import json
import signal
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

from ..logging import get_logger
from ..config import settings

logger = get_logger(__name__)


@dataclass
class QuestionProgress:
    """Progress tracking for a single question."""
    question_id: str
    disease: str
    guideline: str
    question_number: int
    status: str = "pending"  # pending, generated, validated, failed
    generated_at: Optional[str] = None
    validated_at: Optional[str] = None
    file_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class GenerationProgress:
    """Overall progress tracking for the generation run."""
    session_id: str
    started_at: str
    diseases: List[str]
    questions_per_guideline: int
    total_questions_target: int
    questions: Dict[str, QuestionProgress] = field(default_factory=dict)
    last_updated: Optional[str] = None
    status: str = "running"  # running, completed, interrupted, exhausted
    api_key_stats: Optional[Dict] = None
    
    @property
    def generated_count(self) -> int:
        return sum(1 for q in self.questions.values() if q.status in ["generated", "validated"])
    
    @property
    def validated_count(self) -> int:
        return sum(1 for q in self.questions.values() if q.status == "validated")
    
    @property
    def failed_count(self) -> int:
        return sum(1 for q in self.questions.values() if q.status == "failed")
    
    @property
    def pending_count(self) -> int:
        """Count of questions still needing generation (pending + failed)."""
        return sum(1 for q in self.questions.values() if q.status in ["pending", "failed"])

    def get_progress_by_disease(self) -> Dict[str, Dict[str, int]]:
        """Get progress breakdown by disease."""
        progress = {}
        for q in self.questions.values():
            if q.disease not in progress:
                progress[q.disease] = {"generated": 0, "validated": 0, "failed": 0, "pending": 0}
            if q.status in ["generated", "validated"]:
                progress[q.disease]["generated"] += 1
            if q.status == "validated":
                progress[q.disease]["validated"] += 1
            elif q.status == "failed":
                progress[q.disease]["failed"] += 1
            elif q.status == "pending":
                progress[q.disease]["pending"] += 1
        return progress

    def get_progress_by_guideline(self, disease: str) -> Dict[str, Dict[str, int]]:
        """Get progress breakdown by guideline for a specific disease."""
        progress = {}
        for q in self.questions.values():
            if q.disease != disease:
                continue
            if q.guideline not in progress:
                progress[q.guideline] = {"generated": 0, "validated": 0, "failed": 0, "pending": 0}
            if q.status in ["generated", "validated"]:
                progress[q.guideline]["generated"] += 1
            if q.status == "validated":
                progress[q.guideline]["validated"] += 1
            elif q.status == "failed":
                progress[q.guideline]["failed"] += 1
            elif q.status == "pending":
                progress[q.guideline]["pending"] += 1
        return progress


class ProgressTracker:
    """
    Tracks and persists generation progress for graceful shutdown and resume.
    """
    
    _shutdown_requested: bool = False
    _instance: Optional["ProgressTracker"] = None
    
    def __init__(self, progress_file: Optional[str] = None):
        """
        Initialize the progress tracker.
        
        Args:
            progress_file: Path to the progress JSON file.
        """
        self.progress_file = Path(progress_file or settings.progress_file)
        self.progress: Optional[GenerationProgress] = None
        self._setup_signal_handlers()
        ProgressTracker._instance = self
    
    @classmethod
    def get_instance(cls) -> Optional["ProgressTracker"]:
        """Get the singleton instance."""
        return cls._instance
    
    @classmethod
    def is_shutdown_requested(cls) -> bool:
        """Check if shutdown has been requested."""
        return cls._shutdown_requested
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        if sys.platform == "win32":
            # Windows-specific handling
            try:
                signal.signal(signal.SIGBREAK, self._signal_handler)
            except AttributeError:
                pass
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.warning(f"\n⚠️ Shutdown signal received ({signum}). Saving progress...")
        ProgressTracker._shutdown_requested = True
    
    def start_session(
        self,
        diseases: List[str],
        guidelines_per_disease: Dict[str, List[str]],
        questions_per_guideline: int
    ) -> GenerationProgress:
        """
        Start a new generation session.
        
        Args:
            diseases: List of disease names.
            guidelines_per_disease: Dict mapping disease to list of guidelines.
            questions_per_guideline: Number of questions per guideline.
            
        Returns:
            GenerationProgress instance.
        """
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Calculate total questions
        total_questions = sum(
            len(guidelines) * questions_per_guideline 
            for guidelines in guidelines_per_disease.values()
        )
        
        self.progress = GenerationProgress(
            session_id=session_id,
            started_at=datetime.now().isoformat(),
            diseases=diseases,
            questions_per_guideline=questions_per_guideline,
            total_questions_target=total_questions,
        )
        
        # Initialize all questions as pending
        for disease in diseases:
            guidelines = guidelines_per_disease.get(disease, [])
            for guideline in guidelines:
                for i in range(questions_per_guideline):
                    question_id = f"{disease}_{guideline}_{i+1}"
                    self.progress.questions[question_id] = QuestionProgress(
                        question_id=question_id,
                        disease=disease,
                        guideline=guideline,
                        question_number=i + 1,
                    )
        
        self.save_progress()
        logger.info(f"Started new session: {session_id}")
        logger.info(f"Total questions to generate: {total_questions}")
        
        return self.progress
    
    def load_progress(self) -> Optional[GenerationProgress]:
        """
        Load existing progress from file.
        
        Returns:
            GenerationProgress if file exists, None otherwise.
        """
        if not self.progress_file.exists():
            return None
        
        try:
            with open(self.progress_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Reconstruct questions
            questions = {}
            for qid, qdata in data.get("questions", {}).items():
                questions[qid] = QuestionProgress(**qdata)
            
            self.progress = GenerationProgress(
                session_id=data["session_id"],
                started_at=data["started_at"],
                diseases=data["diseases"],
                questions_per_guideline=data["questions_per_guideline"],
                total_questions_target=data["total_questions_target"],
                questions=questions,
                last_updated=data.get("last_updated"),
                status=data.get("status", "running"),
                api_key_stats=data.get("api_key_stats"),
            )
            
            logger.info(f"Loaded progress from session: {self.progress.session_id}")
            logger.info(f"Progress: {self.progress.generated_count}/{self.progress.total_questions_target} generated")
            
            return self.progress
            
        except Exception as e:
            logger.error(f"Failed to load progress file: {e}")
            return None
    
    def save_progress(self):
        """Save current progress to file."""
        if self.progress is None:
            return
        
        self.progress.last_updated = datetime.now().isoformat()
        
        # Create directory if needed
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to dict
        data = {
            "session_id": self.progress.session_id,
            "started_at": self.progress.started_at,
            "diseases": self.progress.diseases,
            "questions_per_guideline": self.progress.questions_per_guideline,
            "total_questions_target": self.progress.total_questions_target,
            "questions": {qid: asdict(q) for qid, q in self.progress.questions.items()},
            "last_updated": self.progress.last_updated,
            "status": self.progress.status,
            "api_key_stats": self.progress.api_key_stats,
            "summary": {
                "generated": self.progress.generated_count,
                "validated": self.progress.validated_count,
                "failed": self.progress.failed_count,
                "pending": self.progress.pending_count,
            }
        }
        
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
        logger.debug(f"Progress saved: {self.progress.generated_count}/{self.progress.total_questions_target}")
    
    def mark_generated(self, question_id: str, file_path: str):
        """Mark a question as generated."""
        if self.progress and question_id in self.progress.questions:
            q = self.progress.questions[question_id]
            q.status = "generated"
            q.generated_at = datetime.now().isoformat()
            q.file_path = file_path
            self.save_progress()
    
    def mark_validated(self, question_id: str):
        """Mark a question as validated."""
        if self.progress and question_id in self.progress.questions:
            q = self.progress.questions[question_id]
            q.status = "validated"
            q.validated_at = datetime.now().isoformat()
            self.save_progress()
    
    def mark_failed(self, question_id: str, error: str):
        """Mark a question as failed."""
        if self.progress and question_id in self.progress.questions:
            q = self.progress.questions[question_id]
            q.status = "failed"
            q.error = error
            self.save_progress()
    
    def get_pending_questions(self) -> List[QuestionProgress]:
        """Get list of pending questions (includes failed questions for retry)."""
        if not self.progress:
            return []
        # Include both "pending" and "failed" statuses so failed questions get regenerated
        return [q for q in self.progress.questions.values() if q.status in ["pending", "failed"]]
    
    def get_generated_questions(self) -> List[QuestionProgress]:
        """Get list of generated but not validated questions."""
        if not self.progress:
            return []
        return [q for q in self.progress.questions.values() if q.status == "generated"]
    
    def set_status(self, status: str, api_key_stats: Optional[Dict] = None):
        """Set the overall session status."""
        if self.progress:
            self.progress.status = status
            if api_key_stats:
                self.progress.api_key_stats = api_key_stats
            self.save_progress()
    
    def print_summary(self):
        """Print a summary of the generation progress."""
        if not self.progress:
            logger.info("No progress to report")
            return
        
        logger.info("\n" + "=" * 70)
        logger.info("GENERATION PROGRESS SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Session ID: {self.progress.session_id}")
        logger.info(f"Status: {self.progress.status.upper()}")
        logger.info(f"Started: {self.progress.started_at}")
        logger.info(f"Last Updated: {self.progress.last_updated}")
        logger.info("")
        logger.info(f"Overall Progress:")
        logger.info(f"  Generated: {self.progress.generated_count}/{self.progress.total_questions_target}")
        logger.info(f"  Validated: {self.progress.validated_count}/{self.progress.total_questions_target}")
        logger.info(f"  Failed: {self.progress.failed_count}")
        logger.info(f"  Pending: {self.progress.pending_count}")
        logger.info("")
        
        # Progress by disease
        disease_progress = self.progress.get_progress_by_disease()
        for disease, stats in disease_progress.items():
            logger.info(f"  {disease}:")
            logger.info(f"    Generated: {stats['generated']}, Validated: {stats['validated']}, Failed: {stats['failed']}")
        
        logger.info("")
        if self.progress.api_key_stats:
            logger.info(f"API Key Usage:")
            for key_info in self.progress.api_key_stats.get("keys", []):
                logger.info(f"  {key_info['key_suffix']}: {key_info['request_count']} requests, status: {key_info['status']}")
        
        logger.info("=" * 70)

"""DateFix: cross-platform media timestamp editing with preview and undo."""

__version__ = "0.2.0"

from .core import (Change, FilePlan, FileResult, Offset, Plan, Request, Result,
                   apply, default_journal_dir, discover, plan_from_dict,
                   plan_to_dict, preview, undo)

__all__ = ["Change", "FilePlan", "FileResult", "Offset", "Plan", "Request", "Result",
           "apply", "default_journal_dir", "discover", "plan_from_dict", "plan_to_dict",
           "preview", "undo"]

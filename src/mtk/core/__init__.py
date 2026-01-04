"""Core data models and database functionality."""

from mtk.core.models import (
    Annotation,
    Attachment,
    Base,
    Collection,
    CustomField,
    Email,
    Person,
    PersonEmail,
    PrivacyRule,
    Tag,
    Thread,
    TopicCluster,
)
from mtk.core.database import Database, get_db, init_db, close_db

__all__ = [
    # Models
    "Base",
    "Email",
    "Person",
    "PersonEmail",
    "Thread",
    "Tag",
    "Attachment",
    "PrivacyRule",
    "Annotation",
    "Collection",
    "CustomField",
    "TopicCluster",
    # Database
    "Database",
    "get_db",
    "init_db",
    "close_db",
]

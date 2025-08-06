"""Document search index management utilities and indexers"""

import logging
from abc import ABC, abstractmethod
from collections import defaultdict

from django.conf import settings

import requests

from core import models, utils

logger = logging.getLogger(__name__)


def get_batch_accesses_by_users_and_teams(doc_ids):
    """Get accesses related to a list of document ids, grouped by users and teams."""
    access_qs = (
        models.DocumentAccess.objects.filter(document_id__in=doc_ids)
        .values("document_id", "user__sub", "team")
        .distinct()
    )

    access_by_document = defaultdict(lambda: {"users": set(), "teams": set()})

    for access in access_qs:
        doc_id = str(access["document_id"])
        user_sub = access["user__sub"]
        team = access["team"]

        if user_sub:
            access_by_document[doc_id]["users"].add(str(user_sub))

        if team:
            access_by_document[doc_id]["teams"].add(team)

    return dict(access_by_document)


class BaseDocumentIndexer(ABC):
    """
    Base class for document indexers.

    Handles batching and access resolution. Subclasses must implement both
    `serialize_document()` and `push()` to define backend-specific behavior.
    """

    def __init__(self, batch_size=None):
        """
        Initialize the indexer.

        Args:
            batch_size (int, optional): Number of documents per batch.
                Defaults to settings.SEARCH_INDEXER_BATCH_SIZE.
        """
        self.batch_size = batch_size or settings.SEARCH_INDEXER_BATCH_SIZE

    def index(self):
        """
        Fetch documents in batches, serialize them, and push to the search backend.
        """
        last_id = 0
        while True:
            documents_batch = list(
                models.Document.objects.filter(
                    id__gt=last_id,
                    deleted_at__isnull=True,
                    ancestors_deleted_at__isnull=True,
                ).order_by("id")[: self.batch_size]
            )

            if not documents_batch:
                break

            doc_ids = [doc.id for doc in documents_batch]
            last_id = doc_ids[-1]
            accesses_by_document = get_batch_accesses_by_users_and_teams(doc_ids)

            serialized_batch = [
                self.serialize_document(document, accesses_by_document)
                for document in documents_batch
            ]
            self.push(serialized_batch)

    @abstractmethod
    def serialize_document(self, document, accesses):
        """
        Convert a Document instance to a JSON-serializable format for indexing.

        Must be implemented by subclasses.
        """

    @abstractmethod
    def push(self, data):
        """
        Push a batch of serialized documents to the backend.

        Must be implemented by subclasses.
        """


class FindDocumentIndexer(BaseDocumentIndexer):
    """
    Document indexer that pushes documents to La Suite Find app.
    """

    def serialize_document(self, document, accesses):
        """
        Convert a Document to the JSON format expected by La Suite Find.

        Args:
            document (Document): The document instance.
            accesses (dict): Mapping of document ID to user/team access.

        Returns:
            dict: A JSON-serializable dictionary.
        """
        doc_id = str(document.id)
        text_content = utils.base64_yjs_to_text(document.content)
        return {
            "id": doc_id,
            "title": document.title,
            "content": text_content,
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
            "users": list(accesses.get(doc_id, {}).get("users", set())),
            "groups": list(accesses.get(doc_id, {}).get("teams", set())),
            "reach": document.link_reach,
            "size": len(text_content.encode("utf-8")),
        }

    # TODO:
    # def search(self, ):
    #     # Include access token (resource server)
    #     find.post(find_url, ...HTTP_AUTHORIZATION=access_token...)

    # def format_response():

    def push(self, data):
        """
        Push a batch of documents to the Find backend.

        Args:
            data (list): List of document dictionaries.
        """
        url = getattr(settings, "SEARCH_INDEXER_URL", None)
        if not url:
            raise RuntimeError(
                "SEARCH_INDEXER_URL must be set in Django settings before indexing."
            )

        secret = getattr(settings, "SEARCH_INDEXER_SECRET", None)
        if not secret:
            raise RuntimeError(
                "SEARCH_INDEXER_SECRET must be set in Django settings before indexing."
            )
        try:
            response = requests.post(
                url,
                json=data,
                headers={"Authorization": f"Bearer {secret}"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.error("HTTPError: %s", e)
            logger.error("Response content: %s", response.text)
            raise

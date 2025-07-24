"""Tests for Documents search indexers"""

from unittest.mock import patch

import pytest

from core import factories, utils
from core.services.search_indexers import FindDocumentIndexer

pytestmark = pytest.mark.django_db


def test_push_raises_error_if_search_indexer_url_is_none(settings):
    """
    Indexer should raise RuntimeError if SEARCH_INDEXER_URL is None or empty.
    """
    settings.SEARCH_INDEXER_URL = None
    indexer = FindDocumentIndexer()

    with pytest.raises(RuntimeError) as exc_info:
        indexer.push([])

    assert "SEARCH_INDEXER_URL must be set in Django settings before indexing." in str(
        exc_info.value
    )


def test_push_raises_error_if_search_indexer_url_is_empty(settings):
    """
    Indexer should raise RuntimeError if SEARCH_INDEXER_URL is empty string.
    """
    settings.SEARCH_INDEXER_URL = ""
    indexer = FindDocumentIndexer()

    with pytest.raises(RuntimeError) as exc_info:
        indexer.push([])

    assert "SEARCH_INDEXER_URL must be set in Django settings before indexing." in str(
        exc_info.value
    )


def test_push_raises_error_if_search_indexer_secret_is_none(settings):
    """
    Indexer should raise RuntimeError if SEARCH_INDEXER_SECRET is None or empty.
    """
    settings.SEARCH_INDEXER_SECRET = None
    indexer = FindDocumentIndexer()

    with pytest.raises(RuntimeError) as exc_info:
        indexer.push([])

    assert (
        "SEARCH_INDEXER_SECRET must be set in Django settings before indexing."
        in str(exc_info.value)
    )


def test_push_raises_error_if_search_indexer_secret_is_empty(settings):
    """
    Indexer should raise RuntimeError if SEARCH_INDEXER_SECRET is empty string.
    """
    settings.SEARCH_INDEXER_SECRET = ""
    indexer = FindDocumentIndexer()

    with pytest.raises(RuntimeError) as exc_info:
        indexer.push([])

    assert (
        "SEARCH_INDEXER_SECRET must be set in Django settings before indexing."
        in str(exc_info.value)
    )


def test_services_search_indexers_serialize_document_returns_expected_json():
    """
    It should serialize documents with correct metadata and access control.
    """
    user_a, user_b = factories.UserFactory.create_batch(2)
    document = factories.DocumentFactory()

    factories.UserDocumentAccessFactory(document=document, user=user_a)
    factories.UserDocumentAccessFactory(document=document, user=user_b)
    factories.TeamDocumentAccessFactory(document=document, team="team1")
    factories.TeamDocumentAccessFactory(document=document, team="team2")

    doc_id = str(document.id)
    accesses = {
        doc_id: {
            "users": {str(user_a.sub), str(user_b.sub)},
            "teams": {"team1", "team2"},
        }
    }

    indexer = FindDocumentIndexer()
    result = indexer.serialize_document(document, accesses)

    assert result["id"] == doc_id
    assert result["title"] == document.title
    assert result["content"] == utils.base64_yjs_to_text(document.content)
    assert result["created_at"] == document.created_at.isoformat()
    assert result["updated_at"] == document.updated_at.isoformat()
    assert set(result["users"]) == {str(user_a.sub), str(user_b.sub)}
    assert set(result["groups"]) == {"team1", "team2"}
    assert result["reach"] == document.link_reach


@patch.object(FindDocumentIndexer, "push")
def test_services_search_indexers_batches_pass_only_batch_accesses(mock_push, settings):
    """
    Documents indexing should be processed in batches,
    and only the access data relevant to each batch should be used.
    """
    settings.SEARCH_INDEXER_BATCH_SIZE = 2
    documents = factories.DocumentFactory.create_batch(5)

    # Attach a single user access to each document
    expected_user_subs = {}
    for document in documents:
        access = factories.UserDocumentAccessFactory(document=document)
        expected_user_subs[str(document.id)] = str(access.user.sub)

    FindDocumentIndexer().index()

    # Should be 3 batches: 2 + 2 + 1
    assert mock_push.call_count == 3

    seen_doc_ids = set()

    for call in mock_push.call_args_list:
        batch = call.args[0]
        assert isinstance(batch, list)

        for doc_json in batch:
            doc_id = doc_json["id"]
            seen_doc_ids.add(doc_id)

            # Only one user expected per document
            assert doc_json["users"] == [expected_user_subs[doc_id]]
            assert doc_json["groups"] == []

    # Make sure all 5 documents were indexed
    assert seen_doc_ids == {str(d.id) for d in documents}


@patch("requests.post")
def test_push_uses_correct_url_and_data(mock_post, settings):
    """
    push() should call requests.post with the correct URL from settings
    the timeout set to 10 seconds and the data as JSON.
    """
    settings.SEARCH_INDEXER_URL = "http://example.com/index"

    indexer = FindDocumentIndexer()
    sample_data = [{"id": "123", "title": "Test"}]

    mock_response = mock_post.return_value
    mock_response.raise_for_status.return_value = None  # No error

    indexer.push(sample_data)

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args

    assert args[0] == settings.SEARCH_INDEXER_URL
    assert kwargs.get("json") == sample_data
    assert kwargs.get("timeout") == 10

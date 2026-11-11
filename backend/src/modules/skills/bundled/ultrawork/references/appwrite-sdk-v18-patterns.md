# Appwrite SDK v18 Patterns (Python)

Discovered during parallel migration of AWUN from Firestore → Appwrite using ultrawork delegation (May 2026).

## Document Structure

The Appwrite Python SDK v18 returns `Document` and `DocumentList` objects — NOT plain dicts.

| What | Type | How to Access |
|------|------|---------------|
| `create_document(...)` | `Document` | `doc.data` → dict of user fields; `doc.id` → `$id`; `doc.createdat` → `$createdAt` |
| `get_document(...)` | `Document` | Same as above |
| `list_documents(...)` | `DocumentList` | `result.documents` → list of Document; `result.total` → int count |
| `update_document(...)` | `Document` | Same as Document above |
| `delete_document(...)` | void | Returns nothing |

**Key differences from dict behavior:**
- ❌ `dict(doc)` only returns metadata fields (id, collectionid, databaseid, createdat, updatedat, permissions) — NOT user data fields
- ❌ `doc["$id"]` raises `TypeError: 'Document' object is not subscriptable`
- ✅ `doc.id` gives the `$id` value
- ✅ `doc.data` is a dict containing all user-stored fields (including `None` values for unset optional attributes)
- ✅ `result.get("documents", [])` raises `AttributeError: 'DocumentList' object has no attribute 'get'`
- ✅ `result.documents` gives the list of documents

### Document-to-Domain Conversion Pattern

```python
def _doc_to_vendor(doc) -> Vendor:
    # doc.data has the user fields, dict(doc) only has metadata
    data = dict(getattr(doc, 'data', {}) or {})

    # Map $id to domain ID if not present
    if "vendor_id" not in data or not data.get("vendor_id"):
        data["vendor_id"] = doc.id

    return Vendor.from_dict(data)
```

## Attribute Schema Management

**Attributes are created asynchronously.** Creating an attribute returns immediately with `status: "processing"`. You must poll until `status: "available"` before using the attribute or creating subsequent ones.

```python
def wait_for_attribute(collection_id, key, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        attr = api("GET", f"/databases/{db_id}/collections/{collection_id}/attributes/{key}")
        if attr.get("status") == "available":
            return attr
        time.sleep(2)  # typically 2-5 seconds
```

**Free tier limit: 13 attributes per collection.** Plan your schema accordingly. Combine infrequently-filtered fields into a single JSON blob stored as a string attribute:

```python
# Queryable fields → top-level attributes (within 13 limit)
TOP_LEVEL = {"product_id", "vendor_id", "title", "status", "price"}

# Everything else → single JSON blob
DATA_BLOB = {"description", "tags", "variants", "images", "source"}

def _product_to_doc(product):
    raw = product.to_dict()
    doc = {}
    blob = {}
    for k, v in raw.items():
        if k in TOP_LEVEL:
            doc[k] = v
        elif k in DATA_BLOB:
            blob[k] = v
    doc["data"] = json.dumps(blob, default=str)
    return doc
```

**Required vs optional attributes:**
- When using auto-generated IDs (`ID.unique()`), make ID fields (`vendor_id`, `product_id`) NOT required in the schema, because the document is created without them and the value is set after creation by mapping `$id` to the domain field.
- Use `x-default` parameter to set default values for optional attributes.

## Collection Creation (REST API)

```bash
POST /v1/databases/{dbId}/collections
-d '{"collectionId": "vendors", "name": "Vendors",
     "permissions": ["create(\"any\")","read(\"any\")","update(\"any\")","delete(\"any\")"],
     "documentSecurity": false}'
```

## Index Management

Indexes are also created asynchronously. Creating a duplicate index returns HTTP 409.

```bash
POST /v1/databases/{dbId}/collections/{collId}/indexes
-d '{"key": "vendor_id_idx", "type": "key", "attributes": ["vendor_id"]}'
```

Compound indexes (e.g., `vendor_id + status`) can be created by listing multiple attributes.

## Common HTTP Error Codes

| Code | Meaning | Handling |
|------|---------|----------|
| 409 | Already exists (collection, attribute, or index) | Safe to skip |
| 400 | Bad request (invalid size, wrong type, limit exceeded) | Check request body |
| 401/403 | Auth failure | Check API key scopes |

## API Key Scopes Needed

For full CRUD operations on databases, collections, documents, and storage:
- `databases.read` + `databases.write`
- `collections.read` + `collections.write`
- `documents.read` + `documents.write`
- `buckets.read` + `buckets.write`
- `files.read` + `files.write`

(For read-only operations, omit the write scopes.)

## SDK Deprecation Warnings

Appwrite SDK v18 emits deprecation warnings for the `Documents` API:
```
DeprecationWarning: Call to deprecated function 'list_documents'. 
  This API has been deprecated since 1.8.0. Please use `tablesDB.list_rows` instead.
```

The same applies to `create_document`, `get_document`, `update_document`, `delete_document`. The new API is `tablesDB.*` with `list_rows`, `create_row`, `get_row`, `update_row`, `delete_row`. Migration to v19 SDK will require updating all repository calls.

## Mock Query Parsing (FakeAppwrite)

When building an in-memory Appwrite mock for tests, Query objects do NOT have Python-accessible attributes like `._method` or `._value`. The Appwrite Query class serializes to a **JSON string** representation:

```python
>>> str(Query.equal("status", "pending"))
'{"method":"equal","attribute":"status","values":["pending"]}'

>>> str(Query.limit(10))
'{"method":"limit","values":[10]}'
```

**To parse queries in a mock**, use `json.loads(str(query))`:

```python
import json

def _apply_query(docs, query):
    q = json.loads(str(query))  # Parse JSON string representation
    method = q["method"]
    
    if method == "equal":
        attr = q["attribute"]
        value = q["values"][0]
        return [d for d in docs if d.get(attr) == value]
    elif method == "notEqual":
        attr = q["attribute"]
        value = q["values"][0]
        return [d for d in docs if d.get(attr) != value]
    elif method == "limit":
        return docs[:q["values"][0]]
    elif method == "offset":
        return docs[q["values"][0]:]
    return docs
```

**Pitfall:** The query object's internal attributes (like `._method`, `._attribute`, `._values`) are implementation details that may differ across SDK versions. The JSON string representation is stable. Always parse via `json.loads(str(query))` in test mocks, not via attribute access.

**Dual-format safety:** If your mock must support both real SDK query objects AND string queries (for flexibility), try JSON parsing first and fall back to attribute access:

```python
def _parse_queries(docs, queries):
    for query in (queries or []):
        try:
            q = json.loads(str(query))
            docs = _apply_json_query(docs, q)
        except (json.JSONDecodeError, TypeError):
            # Fallback: legacy attribute-based query parsing
            docs = _apply_attr_query(docs, query)
    return docs
```

## Mock Error Inheritance (FakeAppwrite)

The mock's error class MUST inherit from `AppwriteException` so that service-layer `except AppwriteException` handlers catch mock errors:

```python
from appwrite.exception import AppwriteException

class _FakeAppwriteError(AppwriteException):
    """Mimics AppwriteException for test purposes."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Appwrite error {code}: {message}")
```

**Pitfall:** If `_FakeAppwriteError` only inherits from `Exception`, service code like `try: ... except AppwriteException: return None` will NOT catch it, causing mock "not found" errors to propagate as 500s instead of graceful None returns. This is easy to miss because the real SDK always raises `AppwriteException` — the mock must match.

## File / Storage Patterns

### `InputFile.from_bytes()` (Required for Bytes Upload)

The Appwrite SDK v18 provides a proper `InputFile` class for file uploads. **Do NOT use a custom `BytesFileWrapper`** — the SDK's `chunked_upload` method checks `input_file.source_type` which must be `'bytes'` or `'path'`. The `InputFile.from_bytes()` factory sets this correctly.

```python
from appwrite.input_file import InputFile

file_obj = InputFile.from_bytes(processed_bytes, filename, mime_type)
result = storage.create_file(
    bucket_id=bucket_id,
    file_id=ID.unique(),
    file=file_obj,
)
```

### `File` Object Access (Same Pattern as Document)

`create_file()` returns a `File` object (Pydantic model), not a dict:
- ❌ `result["$id"]` → `TypeError: 'File' object is not subscriptable`
- ✅ `result.id` → the file's `$id`
- ✅ `result.name` → the filename
- ✅ `result.mime_type` → the MIME type

### View URL Construction

```python
view_url = f"{endpoint}/storage/buckets/{bucket_id}/files/{file_id}/view"
```

### File Deletion

```python
# Delete by file ID
storage.delete_file(bucket_id=BUCKET_ID, file_id=file_id)

# Extract file ID from a view URL
parts = url.split("/")
if "files" in parts:
    idx = parts.index("files")
    file_id = parts[idx + 1]  # URL: .../files/{fileId}/view
```

### Storage Bucket Verification

```python
def verify_bucket():
    """Verify the storage bucket is accessible."""
    result = storage.list_files(bucket_id=bucket_id, queries=[Query.limit(1)])
    return {"status": "healthy", "bucket": bucket_id}
```


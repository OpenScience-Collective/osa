# NEMAR API Reference

Technical reference for the NEMAR (NeuroElectroMagnetic Archive) public API used by the NEMAR community assistant tools.

**Base URL:** `https://nemar.org/api/dataexplorer/datapipeline`
**Authentication:** None required (fully public)
**Only valid table:** `dataexplorer_dataset`

## Endpoints

### 1. List Datasets - `/records`

Fetch paginated dataset records.

```bash
curl --request GET \
  --url 'https://nemar.org/api/dataexplorer/datapipeline/records' \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"dataexplorer_dataset", "start": 0, "limit": 10}'
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | Yes | Must be `"dataexplorer_dataset"` |
| `start` | int | Yes | Pagination offset (0-based) |
| `limit` | int | Yes | Number of records (can use 1000 to get all) |

**Response:**
```json
{
  "total": 485,
  "entries": {
    "0": { /* dataset object */ },
    "1": { /* dataset object */ },
    ...
  },
  "start": 0,
  "limit": 10,
  "success": true
}
```

**Notes:**
- `entries` uses string indices (`"0"`, `"1"`, etc.), not an array
- No server-side search, filter, or sort; must fetch and filter client-side
- Can fetch all datasets in one call with `limit=1000`
- As of 2025, there are ~485 datasets

### 2. Get Dataset by ID - `/datasetid`

Fetch a single dataset by its identifier.

```bash
curl --request GET \
  --url 'https://nemar.org/api/dataexplorer/datapipeline/datasetid' \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"dataexplorer_dataset", "dataset_id": "ds005697"}'
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | Yes | Must be `"dataexplorer_dataset"` |
| `dataset_id` | string | Yes | Dataset ID (e.g., `"ds005697"`) |

**Response:**
```json
{
  "entry": {
    "0": { /* dataset object */ }
  },
  "success": true
}
```

**Notes:**
- Returns empty `entry: {}` for invalid IDs (still `success: true`)
- `entry` uses same string-indexed dict pattern as `entries`

## Dataset Schema

Each dataset has 31 fields:

### Identifiers
| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Dataset ID (e.g., `"ds005697"`) |
| `name` | string | Human-readable name (often descriptive) |
| `created` | string | Creation timestamp (`YYYY-MM-DD HH:MM:SS`) |
| `publishDate` | string | Publication timestamp |
| `uploader` | string | Username of original uploader |
| `latestSnapshot` | string | Version string (e.g., `"1.0.2"`) |
| `DatasetDOI` | string | DOI (e.g., `"doi:10.18112/openneuro.ds005697.v1.0.2"`) |

### BIDS Metadata
| Field | Type | Description |
|-------|------|-------------|
| `BIDSVersion` | string | BIDS spec version (e.g., `"1.8.0"`) |
| `License` | string | Data license (typically `"CC0"`) |
| `Authors` | string | Author list (comma-separated or `===NEMAR-SEP===` delimited) |
| `Acknowledgements` | string | Acknowledgement text |
| `HowToAcknowledge` | string | Citation instructions |
| `Funding` | string | Funding sources (`===NEMAR-SEP===` delimited) |
| `ReferencesAndLinks` | string | URLs/references (`===NEMAR-SEP===` delimited) |
| `EthicsApprovals` | string | Ethics approval information |
| `readme` | string | Full README.md content (can be very long) |

### Experimental Details
| Field | Type | Description |
|-------|------|-------------|
| `tasks` | string | Comma-separated task names (e.g., `"rest, gonogo"`) |
| `modalities` | string | Comma-separated modalities (e.g., `"EEG"`, `"MEG, MRI"`) |
| `HEDVersion` | string | HED schema version (empty if not annotated) |
| `hedAnnotation` | int | `0` or `1` (whether HED annotations are present) |

### Dataset Size
| Field | Type | Description |
|-------|------|-------------|
| `participants` | int | Number of subjects |
| `sessionsNum` | int | Number of sessions |
| `totalFiles` | int | Total file count |
| `file_size` | int | Size in bytes |
| `byte_size_format` | string | Human-readable size (e.g., `"66.6 GB"`) |
| `age_min` | int | Minimum participant age (`0` if unspecified) |
| `age_max` | int | Maximum participant age (`0` if unspecified) |

### Platform Flags
| Field | Type | Description |
|-------|------|-------------|
| `onBrainlife` | int | `0`/`1` - available on Brainlife |
| `local_dataset` | int | `0`/`1` - available locally |
| `processed` | int | `0`/`1` - has processed data |

## Multi-Value Fields

Some fields use `===NEMAR-SEP===` as a delimiter for multiple values:
- `Funding`: Multiple funding sources
- `ReferencesAndLinks`: Multiple URLs/references
- `Authors`: Sometimes (also comma-separated in some datasets)

Example:
```
"NIH R01NS047293===NEMAR-SEP===NSF BCS-0924532===NEMAR-SEP===ONR N00014-16-1-2257"
```

Split on `===NEMAR-SEP===` and strip whitespace from each part.

## URL Patterns

- **NEMAR detail page:** `https://nemar.org/dataexplorer/detail?dataset_id={id}`
- **OpenNeuro page:** `https://openneuro.org/datasets/{id}`
- **OpenNeuro version:** `https://openneuro.org/datasets/{id}/versions/{latestSnapshot}`

## Limitations

1. **No server-side search/filter/sort** - must fetch all and filter client-side
2. **Only one valid table** - `dataexplorer_dataset` (others return validation errors)
3. **Only two endpoints** - `/records` and `/datasetid` (no `/search`, `/tables`, etc.)
4. **GET with body** - API uses GET method but expects JSON body (unusual; works with curl `-d`)
5. **String-indexed responses** - entries/entry use `{"0": ..., "1": ...}` instead of arrays
6. **No rate limiting observed** - but be reasonable with request frequency

## Dataset Statistics (as of early 2025)

- **Total datasets:** ~485
- **Common modalities:** EEG (~53), MEG (~9), MEG+MRI (~7), EEG+MRI (~6), iEEG (~5)
- **Datasets with HED annotations:** ~6
- **Largest by participants:** ds002181 (226), ds003655 (156), ds003474 (122)
- **Common tasks:** rest, noise, gonogo, memory, attention, various experimental paradigms

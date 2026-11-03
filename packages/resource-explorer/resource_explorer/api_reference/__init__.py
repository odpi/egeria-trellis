"""An API reference for a repository, from up to three sources, each labelled by where it came from.

    committed_spec     an OpenAPI/Swagger file committed in the repository (parsed during the survey,
                       from the checkout symbol extraction already holds; `committed.py`)
    running_instance   a document a person registered the URL of and RE fetched read-only (`live.py`)
    derived_from_code  built from the stored route registrations joined to handler signatures
                       (`derived.py`); NOT a published contract

All three produce the same kind-agnostic shape (`model.Interface` / `model.Operation`): an interface has a
kind (`http_api` now), a source, and operations. A later kind (MCP: tools, prompts, resources, each with a
name, description and input schema) adds a parser per source and a kind entry in `model.KINDS`; it does not
reshape the model, the comparison, the storage or the pane. See `model.py`.
"""

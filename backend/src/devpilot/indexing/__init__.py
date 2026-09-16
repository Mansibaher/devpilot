"""Pure indexing logic: file discovery, filtering, and language detection.

Nothing in this package touches the database, the network, or a subprocess.
The rules that decide which files are kept and what language they are live here
as pure functions, so they are testable in milliseconds without a container and
reused unchanged by the worker's indexing service.
"""

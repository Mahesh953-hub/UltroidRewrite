# Vendored Akinator wrapper

This package is the `akipy` 0.1.2 source from <https://github.com/ufoptg/akipy>
(commit `2af0027d05ef32a89aa6dede27a7bde4d8c4ad0e`), retained here to keep
SarahUB's asynchronous Akinator feature intact without installing its stale
`httpx>=0.25,<0.26` distribution requirement. The upstream wrapper uses only
ordinary `httpx` client APIs; SarahUB declares the shared `httpx>=0.28.1`
dependency required by Gemini and tests the async game/transport path against
that client version.

The upstream MIT license and copyright notice are in `LICENSE`.

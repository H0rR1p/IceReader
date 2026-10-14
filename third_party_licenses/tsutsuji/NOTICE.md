# Tsutsuji attribution and changes

日本語機能表現辞書『つつじ』 / Tsutsuji, release 1.1u.

Copyright © 2008 Suguru Matsuyoshi and Satoshi Sato.

Official source: https://sites.google.com/edu.teu.ac.jp/cl-lab/研究/言語資源/日本語機能表現辞書つつじ

The authors distribute the dictionary under Creative Commons Attribution-ShareAlike 4.0 International as of 2023-11-03.
License: https://creativecommons.org/licenses/by-sa/4.0/

IceReader changes: converted the complete nine-level XML hierarchy and inherited attributes to JSON; retained all 16,801 surface-form IDs, source connection definitions and source attributes; added an explicit IPADIC-to-Sudachi connection map and newly written simple Chinese teaching text.

The derived resources/grammar/functional_expressions.json and its Tsutsuji-based hierarchy/connection adaptations are distributed under CC-BY-SA-4.0. Original Chinese explanations in this combined dictionary are likewise made available under CC-BY-SA-4.0. Application code has its separately stated software license.

Fixed archive: tsutsuji-1.1u.zip; SHA-256 `38ecf8f9d147edd7c167d31fa0a8c11648a7896fbdb1d66544aacd98c4697270`.
The corresponding-source package includes this archive under dependency-sources/.

resources/grammar/functional_inventory.json is separately derived from Sudachi core 20250515 by Works Applications, Apache-2.0. IceReader enumerated all word-info records and converted the functional-word subset to JSON. The pinned package source and SudachiPy 0.6.10 binary-format source accompany the dependency sources; the core package downloads its fixed dictionary during installation. Regeneration uses that installed dictionary (or --sudachi-dictionary) and verifies the hash below. Apache attribution and license texts are retained in third_party_licenses/python/sudachidict-core-20250515/.
Sudachi system dictionary SHA-256: `cd6ee0d3d4f038314b16ddf611a776db97c1813f42068a9354b1c17e966aee53`.

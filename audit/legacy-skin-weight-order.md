# Legacy Skin Matrix/Weight pairing

## Source representation

NetP3DLib's `WeightListChunk` reads three semantic floats per vertex as `Vector3(X,Y,Z)`. Its `MatrixListChunk` exposes semantic properties `A,B,C,D` but reads and writes the four raw bytes in `D,C,B,A` order.

Therefore the three explicit weights are semantic `A=X`, `B=Y`, `C=Z`; the omitted fourth weight is semantic `D=1-X-Y-Z`. When preserving the decoder's raw matrix-byte order `D,C,B,A`, the corresponding weight order is:

```text
[1-X-Y-Z, Z, Y, X]
```

The previous decoder order `[1-X-Y-Z, Z, X, Y]` incorrectly exchanged the semantic A and B weights.

## Independent deformation check

The source-derived order was checked rather than selected by appearance. For `AlexVestShape`, all 24 assignments of its four decoded weights to the raw `D,C,B,A` joint slots were evaluated at 0.42 seconds of the source `alex_pnch_3hit_combo` pose. For each assignment, posed-to-bind edge-length ratios were measured across all indexed triangle edges.

The source-derived identity assignment after correction was the unique minimum:

- 99th percentile ratio: `2.0280392812786117`
- 99.9th percentile ratio: `3.225747708690093`
- maximum ratio: `3.6409626283525536`

The next candidate had `2.335329869234717 / 5.39935312030144 / 7.511928390294206`. The former decoder assignment, represented as the A/B swap against corrected output, produced `5.833887868730616 / 9.327092079782565 / 14.048166391292975` in the original run.

This establishes the correct legacy influence pairing and removes one independent deformation error. It did **not** establish the visible back-texture artifact's root cause: a subsequent live screenshot proved that artifact remained. The visible atlas corruption was later traced to the legacy UV_List parser skipping only the count while failing to skip the second `Channel` u32 header.

## Implementation

`tools/entities/entity_decoder.py::_legacy_skin_weights` now returns `[implicit, w2, w1, w0]`. A direct semantic-order regression assertion was added to `test_entity_decoder.py`. Existing protected source archives are unchanged.

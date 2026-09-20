# AlexVestShape legacy UV_List correction

A post-deploy screenshot proved the back atlas corruption remained after correcting legacy skin influence pairing. The screenshot showed unrelated white shirt/cloth regions sampled onto the jacket, while the decoded `alex_body_dm` texture itself contained the correct red back emblem.

NetP3DLib `UVListChunk` serializes:

```text
u32 NumUVs
u32 Channel
Vector2 UVs[NumUVs]
```

The legacy decoder used `payload[4:]`, skipping `NumUVs` but not `Channel`. That shifted the whole float stream by four bytes: the first decoded pair was `(reinterpret(Channel), source_U0)`, the next was `(source_V0, source_U1)`, and so on. This exactly explains atlas regions alternating across triangles.

The decoder now selects a complete source channel 0 and copies Vector2 bytes from offset 8. AlexVestShape's corrected leading UV pairs are approximately `(0.04537,0.35707)`, `(0.04105,0.35207)`, `(0.01564,0.38347)`, matching the red jacket-emblem atlas region. Its full UV range is 0.00644..0.94667. A synthetic regression asserts both header words are skipped.

The earlier skin-weight correction remains independently valid, but it was not the complete cause of the visible texture artifact. This document supersedes any prior claim that UVs had been ruled out.

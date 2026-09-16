# Deterministic packaging preview

`src/PackagingPreview.tsx` consumes the same Scene contract and server-generated geometry assembly transforms used by PDF output. It renders all faces, text, original images, vector barcode modules and mirrored physical holes from that data. It does not generate AI mockups. Labels/arrows are enabled with `verificationMode`.

The component exports named/default `PackagingPreview` with `{scene, geometry, onFaceSelect, assetUrl?, verificationMode?}`. Parent apps should load the bundled static NotoSansKR web font and pass the authorized asset endpoint URL resolver. Scene/geometry updates regenerate textures. Face clicks and accessible face buttons call `onFaceSelect`. Canvas textures become 2D fallback previews when WebGL is unavailable.

Three.js 0.186.0 is used directly in React useEffect. React Three Fiber 9.7.0 currently requires React <19.3 while this app uses React 19.3; peer dependencies were not forced and React was not downgraded. Geometry, material, texture, controls, renderer, animation and resize resources are disposed on teardown.

EAN-13 preview encoding is a deterministic mirror parity-tested against pinned ReportLab. Export authority remains the Python server. Barcode numbers remain strings, preserve leading zeroes, validate check digits and cannot use arbitrary scaling.

These are explicit demo panel assemblies. Pouch bulging, filling, gusset collapse, paper thickness and physical manufacturing fitness are not simulated. The server's assumptions are shown beside the preview.

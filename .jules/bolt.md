## 2024-03-24 - [Regex Filtering Optimization]
**Learning:** In PySide6 log viewer applications dealing with huge (32MB+) byte chunks, splitting strings into lines via `chunk.split(b"\n")` for iterative regex matching is a major performance bottleneck due to massive allocation of temporary byte objects.
**Action:** Always prefer executing regex searches or string `find()` directly on the full chunk buffer, and manually advance the line counter by computing `chunk.count(b"\n", start_pos, end_pos)`. This pushes the heavy lifting to C-optimized implementations.

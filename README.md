# eda2eagle

> **Status:** Experimental · Ambitious · Work-in-progress

Universal netlist-to-Eagle-CAD-XML converter, and the **first stepping stone** toward a much larger vision: turning a **vector PDF of a hand-drawn or printed schematic** into a fully editable, netlist-aware EDA project.

## The vision

This project is not just a file-format translator. It is the foundation layer of a longer roadmap:

1. **Phase 1 — current:** parse structured netlists (KiCad, SPICE, PADS ASCII, CadStar) and emit valid Eagle 9.6 XML, with auto-generated symbols, auto-placement, and net sanitization.
2. **Phase 2 — next:** ingest **vector PDFs** of schematics (exported from unknown EDAs, datasheets, old EDA tools, or photographed drawings vectorized via Inkscape/Potrace), extract wires, junctions, component symbols, and pin labels using geometric + topological heuristics.
3. **Phase 3 — goal:** reconstruct a full, editable, netlist-driven `.sch` from what was originally "paper" — so that legacy schematics, whiteboard designs, and scanned archives become first-class EDA projects again.

The output format is deliberately **Eagle XML** because it is the de-facto interchange lingua franca: the same `.sch` file is natively importable by:

| EDA | Import path |
|---|---|
| **KiCad** | File → Import → Non-KiCad Project → Eagle |
| **Altium Designer** | File → Import → Eagle |
| **OrCAD / Allegro** | via Eagle importer or ULPP bridge |
| **EasyEDA / LCEDA** | Import Eagle `.sch` / `.brd` |
| **Autodesk Fusion 360 Electronics** | native Eagle engine |
| **CircuitStudio** | Eagle import |
| **LibrePCB** | via Eagle compatibility layer |

In other words: one converter, **every major EDA** — free or commercial.

## What it does today

- **Format autodetection** from file content (not extension): KiCad `.net`, SPICE `.cir`, PADS ASCII `.asc`, CadStar `.txt`.
- **S-expression parser** for KiCad, dedicated parsers for the other three formats.
- **Automatic symbol generation:** rectangular bodies with pins evenly distributed on two sides, sized to fit net labels without overlap.
- **Auto-placement** using a graph-based spiral algorithm (requires `networkx`); falls back to a 15-column grid otherwise.
- **Net sanitization:** names uppercased, illegal characters replaced, collisions disambiguated.
- **Preview rendering** with `matplotlib` so you can tune clearance before committing to the final file.
- **Dual interface:** CLI for scripting, GUI (tkinter) for double-click usage on Windows.

## What it does NOT do (yet)

- It does **not** read PDFs, images, or raster scans.
- It does **not** perform OCR or symbol recognition.
- It does **not** reconstruct placement from visual cues — placement is purely topological today.

Those are exactly the problems Phase 2 and 3 are meant to solve.

## Requirements

- Python 3.7+
- `networkx` (optional, enables smart placement)
- `matplotlib` (optional, enables visual preview)

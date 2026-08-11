# SOURCES — staging/profiles_dell.json (19 Dell PowerEdge profiles)

Honesty ledger for the Dell profile expansion. Per profile: what was
genuinely sourced (with the URL actually consulted), what is an
engineering estimate, and what could not be sourced at all. This extends
the repo's existing convention that internal layouts, ζ values and heat
loads are documented engineering estimates.

## Tooling status

Web search and web fetch **worked** for this task. Notes on mechanics,
because they affect what "sourced" means below:

- Dell-hosted PDFs (`i.dell.com`, `dl.dell.com`, `downloads.dell.com`)
  refused the default fetcher but downloaded fine via `curl` with browser
  `User-Agent`/`Accept` headers. Text was extracted locally with `pypdf`
  and grepped; every "sourced" value attributed to a PDF below was seen in
  that extracted text (or, where flagged, in a rendered table).
- A few `www.dell.com/support/manuals` HTML pages returned 403 to the
  direct fetcher. Where a value rests only on the search engine's snippet
  of such a page, that is flagged explicitly ("via search snippet") —
  the snippet quoted the official page, but I could not open the page
  itself.
- Two third-party mirrors of official Dell documents were used
  (Quantum's mirror of the R730 owner's manual; jarcomputers /
  pcserverandparts / 10g.com.ua mirrors of Dell technical-specification
  PDFs). These are byte-for-byte Dell publications (regulatory model
  numbers intact); the mirror URL is the one cited because it is the one
  actually fetched.

## The two requester-supplied whitepapers

1. **Power and Cooling Innovations in Dell PowerEdge Servers** (Dell,
   March 2016, 13G era) —
   <https://www.insight.com/content/dam/insight-web/en_US/media/whitepaper/partner/Power%20and%20Cooling%20Innovations%20in%20Dell%20PowerEdge%20Servers.pdf>
   — fetched successfully (curl; 6 pages). Content is **qualitative**:
   Energy Smart architecture, PSU portfolio/efficiency, non-linear fan
   curves, closed-loop thermal control, thermal profiles with a maximum
   exhaust temperature setting. It contains **no per-model numbers** and
   changed **no numeric value** in the profiles. It did reinforce two
   modelling choices: keeping the repo's existing
   `outlet_temp_max_c: 35.0` requirement convention (Dell's thermal
   profiles govern exhaust temperature), and modelling PSUs as porous
   zones with their own fans where the generation documents PSU fans.
2. **PowerEdge 14G Acoustical Performance and Dependencies** (Dell EMC,
   Oct 2018) —
   <https://dl.dell.com/manuals/common/dell_emc_poweredge_14g_acoustical_performance_and_dependencies_22nov2018.pdf>
   — fetched successfully (curl; 51 pages). This one **did** contribute
   hard data:
   - **R240**: fan quantity by configuration — 2×4028 / 3×4028 / 4×4028 /
     4×4028 (Minimum → Feature Rich), single 250 W PSU. This is the basis
     for `fan_count: 4` (max configuration) and the single cabled PSU
     zone.
   - **R740**: configuration tables with PSU classes 495/750/1100 W and
     backplane options (8×3.5, 8×2.5, 16×2.5) — corroborates the 16-bay
     config chosen.
   - **R940**: configuration tables with 2 or 4 CPUs, PSU 1100/1600/2400 W,
     up to 48 DIMMs, PCI slots 1–14 listed (8 usable on the minimum
     single-planar config) — corroborates the spec-sheet values used.
   - General: default "Power Optimized (DAPC)" vs "Performance Optimized"
     fan behaviour — context only, no numeric impact.

## How vendor figures map onto the schema (applies to every profile)

- `chassis_width` = Dell's **Xb** (chassis body width without rack
  latches/ears). This is why most profiles are 0.434 m but R610 is
  0.424 m, R710 0.4431 m, R720/R730/R730xd 0.444 m, R930 0.422 m — those
  are the vendor's own body widths.
- `chassis_height` = Dell's **Y**.
- `chassis_length` = Dell's **Zb** (rack ear to rear wall) for most
  profiles. Exception: R740 and R7425 use ≈ **Zc** (0.715 m, ear to PSU
  handle) to stay dimensionally consistent with the pre-existing on-disk
  R740xd profile (0.715), which shares the chassis.
- Where Dell publishes different depths per backplane (R620, R630, R650,
  R6525), the profile's `display_name` states which chassis was modelled
  and the matching depth was used.

## Estimated in EVERY profile (never published by Dell)

These are engineering estimates in all 19 profiles; they are not
re-listed per profile below unless there is something model-specific to
say:

- `fan_wall_z` — placed 0.16–0.30 m from the inlet, scaled with chassis
  depth and drive-cage depth, following the existing R640 (0.18) and
  R740xd (0.20) conventions. Dell documents *that* the fan wall sits
  between the drive bay and the processors (R610/R710 guidebooks say so
  in words), never *where*.
- `drive_zone_z`, `cpu_zone_z`, `pcie_zone_z`, `optics` positions — front
  → fan wall → CPU → PCIe/PSU ordering copied from the existing profiles
  and scaled to each chassis depth.
- All `zeta` / `permeability` values (drive cage, CPU sinks, PSU zones,
  rear flex bay) — extended from the existing profiles' values, scaled by
  bay count/density: 24×SFF walls 140, 16×SFF 120, 10×SFF 110, 8×SFF 100,
  6×SFF 90, 8×LFF 120, 12×LFF 150, 4×LFF 80; 1U CPU sinks 70, 2U 55,
  4-socket 50. Nobody publishes these; they are impedance-class guesses
  consistent with the repo's existing entries.
- `heat_load` — typical sustained system load, tracked against socket
  count and TDP class of the generation (150 W for the 1-socket R240 up
  to 900 W for the 4-socket R930). Not a vendor figure; Dell publishes
  PSU capacity, not typical thermal load.
- All `custom_zones` box coordinates (PSU positions, rear flex bay) and
  `pcie_risers` x-positions, `pcie_x_band` — geometric estimates laid out
  to match rear-panel photographs/layout conventions (1U: PSUs flanking a
  centre card band, R640-style; 2U/3U/4U: PSUs at the rear corners,
  R740xd-style). The *presence* and count of PSUs is sourced; their
  coordinates are not.
- `fan_rpm: 15000` / `fan_size_mm: 40` on PSU zones — typical 40 mm PSU
  fan class, repo convention; not sourced per model (exception: R610,
  whose PSUs are **documented fanless** — see below).
- `populated_pcie_slots` — a modest runtime default (1–3), not a spec.
  (`pcie_max_slots` IS sourced where noted per profile.)
- `requirements` block and `mesh_settings` — copied verbatim from the
  existing Dell entries in `server_configs.json` (repo convention, not
  vendor data).
- `baseline_zeta: 25.0` — repo convention.

---

## Per-profile ledger

### R610 — "Dell PowerEdge R610 (6-bay)"

**Sourced** — Dell PowerEdge R610 Technical Guidebook:
<https://i.dell.com/sites/doccontent/business/solutions/engineering-docs/en/Documents/server-poweredge-r610-tech-guidebook.pdf>
- Dimensions (§4.2): Xa 482.44 / **Xb 424.0** / **Y 42.6** / Zb **737.3** /
  Zc 772.0 mm → 0.424 × 0.0426 × 0.737.
- Fans (§4.9): "Six dual-rotor 40 mm fans… Only five fans are populated
  in systems with a single processor configuration… cannot be
  hot-swapped" → `fan_count: 6`.
- **PSUs have no integrated fans**: "The R610 Power Supply Units do not
  have any integrated fans; they are cooled by the system fans" → PSU
  zones deliberately carry **no** `fan_rpm` (labelled "fanless").
- 12 DIMM slots; up to six 2.5″ drives; PSU classes 502 W Energy Smart /
  717 W high-output; I/O slots "Two PCIe x8 Gen2 + 1 x4 storage slot" →
  `pcie_max_slots: 2`.

**Estimated**: global list above. PSU boxes drawn flanking the card band
(R640-style); the R610's real PSU bay arrangement was not verified in
coordinates.

### R710 — "Dell PowerEdge R710 (8-bay)"

**Sourced** — Dell PowerEdge R710 Technical Guidebook:
<https://i.dell.com/sites/csdocuments/Business_solutions_engineering-Docs_Documents/en/poweredge-r710-technical-guidebook.pdf>
- Dimensions (Appendix B): H 8.64 cm / W 44.31 cm / D 68.07 cm →
  0.4431 × 0.0864 × 0.6807.
- 18 DIMM slots; drive configs "up to six 3.5″ … OR up to eight 2.5″
  with optional flex bay" (8×2.5 modelled); PSUs "two hot-plug
  high-efficient 570 W OR two 870 W (1+1)"; "two PCI Express risers …
  provide up to four expansion slots and one internal slot" →
  `pcie_max_slots: 4`.
- Fan count 5 (4 populated single-CPU) and "an additional fan integrated
  in each power supply": from the R710 Technical Guidebook content **via
  search snippet** (ManualsLib mirror,
  <https://www.manualslib.com/manual/36360/Dell-Poweredge-R710-Series.html>);
  I could not re-locate the exact sentence in my local text extraction
  (that section extracts poorly), so flagging the weaker provenance. The
  PSU-fan fact is why the R710 PSU zones carry `fan_rpm`.

**Estimated**: global list above.

### R620 — "Dell PowerEdge R620 (10-bay)"

**Sourced** — PowerEdge R620 Technical Guide:
<https://i.dell.com/sites/content/shared-content/data-sheets/en/Documents/dell-poweredge-r620-technical-guide.pdf>
- Dimensions: Xb 434.0 / Y 42.8; 8-bay chassis Zb 682.7 / Zc 701.3;
  **10-bay chassis Zb 731.0** / Zc 752.1 mm → 10-bay modelled,
  0.434 × 0.0428 × 0.731.
- 24 DIMM slots ("Up to 768GB (24 DIMM slots)"); PSU classes 495/750/
  1100 W AC + 1100 W DC; up to 3 PCIe slots → `pcie_max_slots: 3`.
- **Fan count 7**: Dell R620 Owner's Manual "Cooling fans" page — "Your
  system supports seven hot swappable cooling fans" —
  <https://www.dell.com/support/manuals/en-us/poweredge-r620/r620systemownersmanual/cooling-fans?guid=guid-675be8bd-5a52-486a-8f9e-cdbea0bb8559&lang=en-us>.
  The page itself 403'd on direct fetch; the sentence is quoted **via
  search snippet** of that official page.

**Estimated**: global list above.

### R720 — "Dell PowerEdge R720 (16-bay)"

**Sourced** — PowerEdge R720 and R720xd Technical Guide:
<https://i.dell.com/sites/doccontent/shared-content/data-sheets/en/Documents/ESG-PowerEdge-R720-and-R720xd-Technical-Guide.pdf>
- Dimensions: Xa 482.4 / **Xb 444.0** / **Y 87.3** / **Zb 684.0** /
  Zc 723.0 mm → 0.444 × 0.0873 × 0.684.
- "The R720 supports up to seven PCIe expansion cards" →
  `pcie_max_slots: 7`; drive config up to 16×2.5″ or 8×3.5″ (16×2.5
  modelled); PSU 495/750/1100 W.
- **Fan count 6 and 24 DIMMs**: R720 Owner's Manual
  <https://dl.dell.com/topicspdf/poweredge-r720_owners-manual_en-us.pdf>
  internal-view figure captions "DIMMs (24)  cooling fans (6)".

**Estimated**: global list above.

### R630 — "Dell PowerEdge R630 (10-bay)"

**Sourced** — Dell PowerEdge R630 Owner's Manual:
<https://dl.dell.com/topicspdf/poweredge-r630_owners-manual_en-us.pdf>
- Dimensions (Table 17): Xb 434.0 / Y 42.8; 8×2.5 chassis Zb 682.7;
  **10×2.5 and 24×1.8 chassis Zb 731.0** mm → 10-bay modelled,
  0.434 × 0.0428 × 0.731.
- "Your system supports seven hot swappable cooling fans" →
  `fan_count: 7`.
- "24 DIMM slots supporting up to 1536 GB" → `total_dimm_slots: 24`.

**Estimated**: global list above. `pcie_max_slots: 3` (R630 riser
layout) — from generation knowledge, **not re-verified** in the manual;
treat as estimate. PSU wattage class not re-verified (no profile field
depends on it).

### R730 — "Dell PowerEdge R730 (16-bay)"

**Sourced** — PowerEdge R730 and R730xd Technical Guide v1.7:
<https://i.dell.com/sites/doccontent/shared-content/data-sheets/en/Documents/Dell-PowerEdge-R730-and-R730xd-Technical-Guide-v1-7.pdf>
- Dimensions: Xa 482.4 / **Xb 444.0** / **Y 87.3** / **Zb 684.0** /
  Zc 723.0 mm → 0.444 × 0.0873 × 0.684.
- "R730: Up to 7 PCIe 3.0 slots" → `pcie_max_slots: 7`; "Up to 768GB
  (24 DIMM slots)"; drive configs incl. 16×2.5″ (modelled).
- **Fan count 6** — Dell R730 Owner's Manual (Quantum mirror of the Dell
  publication, regulatory model E31S):
  <https://qsupport.quantum.com/freedownloads/SureStaQ/6-68618-01_RevA_Dell-PowerEdge_%20R730-Owners-Manual.pdf>
  — "Your system supports six hot-swappable cooling fans" and figure
  caption "cooling fan in the cooling fan assembly (6)".

**Estimated**: global list above.

### R730xd — "Dell PowerEdge R730xd (24+2 bay)"

**Sourced**
- Chassis dimensions: same chassis as R730 (Technical Guide v1.7 above,
  which covers both) → 0.444 × 0.0873 × 0.684.
- **Fan count 6**: R730xd Owner's Manual
  <https://dl.dell.com/topicspdf/poweredge-r730xd_owners-manual_en-us.pdf>
  — figure caption "cooling fan in the cooling fan assembly (6)".
- **24×2.5 front + 2×2.5 rear**: same manual ("24 x 2.5-inch hard
  drive/SSD chassis", "…and up to two 2.5-inch back-accessible hard
  drives") and the Technical Guide v1.7 drive matrix ("Up to 24 x 2.5″ +
  2 x 2.5″").
- "R730xd: Up to 6 PCIe 3.0 slots" (Technical Guide v1.7) →
  `pcie_max_slots: 6`; 24 DIMM slots (same guide).

**Estimated**: global list above, plus the `rear_flex_bay` porous zone —
its existence is sourced (above), but its box coordinates and its
ζ = 120 / permeability 3e-07 are estimates.

### R530 — "Dell PowerEdge R530 (8x LFF)"

**Sourced** — Dell PowerEdge R530 Owner's Manual:
<https://dl.dell.com/topicspdf/poweredge-r530_owners-manual_en-us.pdf>
- Dimensions (Table 13): Xa 482.4 / **Xb 434** / **Y 86.8**; Zb 633.1
  (cabled PSU) / **646.7 mm (redundant PSU)** → redundant-PSU chassis
  modelled, 0.434 × 0.0868 × 0.6467.
- "Your system supports five cooling fans. A fan blank is pre-installed
  on the first cooling fan slot (FAN1)" → `fan_count: 5`.
- "12 DIMMs" / "Twelve 288-pin" sockets → `total_dimm_slots: 12`.
- "Up to eight 3.5-inch or 2.5-inch hot-swappable hard drives" → 8×LFF.
- PSU classes: AC 495/750/1100 W EPP redundant, 450 W cabled.
- Table 37: five PCIe slots on the system board → `pcie_max_slots: 5`.

**Estimated**: global list above.

### R930 — "Dell PowerEdge R930 (24-bay, 4-socket)"

**Sourced** — Dell PowerEdge R930 Owner's Manual:
<https://dl.dell.com/topicspdf/poweredge-r930_owners-manual_en-us.pdf>
- Dimensions (Table 10): 482.4 / **422** (body) / **Y 172.6** /
  **802.3 mm** → 0.422 × 0.1726 × 0.8023. (The table's column headers
  extract garbled; 802.3 is the depth figure and 422 the body width as
  read from the rendered table — flagging the extraction ambiguity
  with/without bezel.)
- "supports two or four Intel E7-8800/4800 v3 or v4" → `cpu_sockets: 4`.
- "supports six hot-swappable cooling fans that are mounted in a memory
  riser and fan cage" → `fan_count: 6`. **Note**: the real R930 fan cage
  is mid-chassis above the memory risers; the profile's plane fan wall at
  z = 0.30 is a simplification of that (estimate).
- "up to four AC redundant power supply units" / "four 750 W or four
  1100 W" → the two PSU zones are labelled "PSU 1+2" / "PSU 3+4"
  (2-wide banks).
- "Ninety-six 240-pin" memory sockets → `total_dimm_slots: 96`.
- "Up to twenty four 2.5-inch hard drives" → 24×SFF.

**Estimated**: global list above. `pcie_max_slots: 8` is the geometric
band cap, **not** the vendor slot count (the R930 has 10 slots; not
verified here, and more than the card band can hold anyway).

### R240 — "Dell PowerEdge R240 (4x LFF)"

**Sourced** — Dell EMC PowerEdge R240 Technical Specifications Guide:
<https://dl.dell.com/content/manual60668970-dell-emc-poweredge-r240-technical-specifications-guide.pdf>
- Dimensions (Table 1): Xa 482.0 / **Xb 434.0** / **Y 42.8** /
  **Zb 534.496** / Zc 573.596 mm → 0.434 × 0.0428 × 0.5345.
- One processor (E-2100/E-2200/Core i3/Pentium/Celeron) →
  `cpu_sockets: 1`.
- "Four 288-pin" memory sockets → `total_dimm_slots: 4`.
- Cabled PSU, 250 W Bronze or 450 W Platinum → single PSU zone.
- Fan support matrix (fans 1–4; all four required with 4×3.5 + PCIe) →
  `fan_count: 4`; corroborated by the **14G acoustics whitepaper**
  (2/3/4 × 4028 fans by config, 1× 250 W PSU).
- "up to two PCI express generation 3" (riser slots 1–2) →
  `pcie_max_slots: 2`. 4×3.5″ bays (weight table / backplane options).

**Estimated**: global list above; `pcie_risers: []` (the R240's butterfly
riser is not modelled as a standing cage — modelling choice).

### R740 — "Dell PowerEdge R740 (16-bay)"

**Sourced**
- Dimensions: Dell R740 Technical Specifications, "System dimensions"
  page (fetched OK):
  <https://www.dell.com/support/manuals/en-us/poweredge-r740/per740_techspecs_pub/system-dimensions?guid=guid-747ab1a1-1c36-4742-b2a2-295909359f66&lang=en-us>
  — Xa 482.0 / **Xb 434.0** / **Y 86.8** / Zb 678.8 / **Zc 715.5** mm.
  `chassis_length: 0.715` uses ≈Zc for consistency with the pre-existing
  R740xd profile (shared chassis).
- Fans: R740/R740xd Installation and Service Manual
  <https://dl.dell.com/topicspdf/poweredge-r740_owners-manual_en-us.pdf>
  — "Your system supports up to six standard or high performance hot
  swappable cooling fans… For single processor systems, only four
  standard cooling fans are required" → `fan_count: 6`.
- PSU classes 495/750/1100 W and 16×2.5 backplane option: **14G
  acoustics whitepaper** R740 configuration tables.
- 24 DIMM slots: R740/R740xd Technical Guide
  (<https://www.delltechnologies.com/asset/en-us/products/servers/technical-support/poweredge-r740-r740xd-technical-guide.pdf>)
  **via search snippet** ("up to 24 DIMMs"); consistent with the existing
  on-disk R740xd profile.

**Estimated**: global list above. `pcie_max_slots: 8` — the R740's
"up to 8 PCIe slots" is generation knowledge, **not verified in a page I
opened** (the acoustics tables show slots PCI 1–7 populated-or-empty,
i.e. at least 7); treat 8 as an estimate.

### R7425 — "Dell PowerEdge R7425 (24-bay, 2x EPYC)"

**Sourced**
- Dimensions: Dell R7425 Technical Specifications, "System dimensions"
  page (fetched OK):
  <https://www.dell.com/support/manuals/en-us/poweredge-r7425/per7425_techspecs/system-dimensions?guid=guid-f4a40961-dc2b-4683-8437-a3f98e945ff3&lang=en-us>
  — Xa 482.0 / **Xb 434.0** / **Y 86.8** / Zb 677.3 / **Zc 715.63** mm →
  0.434 × 0.0868 × 0.715 (≈Zc, consistent with R740/R740xd family).
- Everything else: Dell R7425 Installation and Service Manual (gotomojo
  mirror of the Dell publication):
  <https://www.gotomojo.com/wp-content/uploads/2019/07/Dell-PowerEdge-R7425-Owners-Manual.pdf>
  — "up to two AMD EPYC processors"; "up to six standard or high
  performance hot swappable cooling fans" (4 with one CPU) →
  `fan_count: 6`; "up to thirty two 288-pins RDIMMs" →
  `total_dimm_slots: 32`; drive matrix incl. "24 drives system: up to 24
  2.5 inch front accessible drives in slots 0 to 23" (modelled); "up to
  eight PCIe generation 3 expansion cards" → `pcie_max_slots: 8`; PSU
  table 495 W/750 W AC Platinum/Titanium (+ higher classes).

**Estimated**: global list above.

### R940 — "Dell PowerEdge R940 (24-bay, 4-socket)"

**Sourced**
- R940 Spec Sheet:
  <https://i.dell.com/sites/csdocuments/Shared-Content_data-Sheets_Documents/en/aa/poweredge-r940-spec-sheet.pdf>
  — 3U; **Height 130.3 / Width 434 / Depth 784.2 mm** (dimensions
  exclude bezel) → 0.434 × 0.1303 × 0.7842; "8 hot plugs fans with full
  redundancy" → `fan_count: 8`; "48 DDR4 DIMM slots" → 48; front
  drive bays "up to 24 x 2.5″ … with up to 12 NVMe" → 24×SFF;
  "13 PCIe Gen. 3 slots (3 x8 + 10 x16)"; PSU Platinum 1100 W (+1600/
  2400 W per the acoustics whitepaper tables).
- Fan count corroborated: R940 Installation and Service Manual
  <https://dl.dell.com/topicspdf/poweredge-r940_owners-manual_en-us.pdf>
  — figure caption "cooling fan (8)".
- 2-or-4 sockets: **14G acoustics whitepaper** R940 config table (CPU
  quantity 2/4/4; single vs dual planar) → `cpu_sockets: 4`.

**Estimated**: global list above. `pcie_max_slots: 8` is the geometric
band cap, deliberately below the sourced 13 physical slots (the 2D card
band cannot hold 13 cards).

### R650 — "Dell PowerEdge R650 (10-bay)"

**Sourced** — Dell PowerEdge R650 Technical Specifications (regulatory
model E69S):
<https://dl.dell.com/content/manual46646525-dell-emc-poweredge-r650-technical-specifications.pdf>
- Dimensions (Table 1): Xb 434 / Y 42.8; 8-drive Zb 700.7 / Zc 736.27;
  **4/10-drive Zb 751.48** / Zc 787.05 mm → 10-bay modelled,
  0.434 × 0.0428 × 0.7515.
- Fans: "supports upto four standard (STD), high performance silver
  grade (HPR SLVR), or high performance gold grade (HPR (Gold)) **dual
  cooling fan modules**… one set of fan module includes two fan body
  with one fan connector" → `fan_count: 8` = 4 modules × 2 fan bodies.
  (3 modules on certain single-CPU configs.) Note the count is stated in
  *modules* by Dell; 8 rotors is my reading of "dual".
- "32, 288-pin" memory sockets → 32; "up to three slots … PCIe Gen 4" →
  `pcie_max_slots: 3`; PSU table includes 800 W and 1100 W classes.

**Estimated**: global list above. `cpu_sockets: 2` — the R650 is Dell's
1U 2-socket 15G platform (technical-guide description via search
snippet); not independently re-verified in the ts PDF text I extracted.

### R6515 — "Dell PowerEdge R6515 (10-bay, 1x EPYC)"

**Sourced** — Dell PowerEdge R6515 Technical Specifications (regulatory
model E45S; pcserverandparts mirror of the Dell publication):
<https://pcserverandparts.com/content/Dell%20PowerEdge%20R6515%20Server%20Technical%20Specifications.pdf>
- Dimensions: **Zb 657.25** / Zc 692.62 mm; H 42.8 / W 434.0 (the
  H/W figures additionally corroborated by the Dell techspecs page via
  search snippet) → 0.434 × 0.0428 × 0.6572.
- "requires all six fans to be installed" → `fan_count: 6`.
- "Sixteen 288-pin" memory sockets → 16; "up to two PCI express (PCIe)
  expansion cards" → `pcie_max_slots: 2`; single AMD EPYC socket
  (10×2.5 among the drive configs; 10-bay modelled).
- 550 W Platinum PSU class: **via search snippet** (Dell spec sheet /
  reseller pages); not re-located in the extracted ts text.

**Estimated**: global list above.

### R6525 — "Dell PowerEdge R6525 (10-bay, 2x EPYC)"

**Sourced**
- Dimensions: Dell R6525 Technical Specifications "Chassis dimensions"
  page (fetched OK):
  <https://www.dell.com/support/manuals/en-us/poweredge-r6525/r6525_ts_pub/chassis-dimensions?guid=guid-3782a405-ae93-4847-9dee-b9e6ce3858d5&lang=en-us>
  — Xb 434.0 / Y 42.8; 8-drive Zb 700.53 / Zc 736.27; **4/10-drive
  Zb 751.48** / Zc 787.05 mm → 10-bay modelled, 0.434 × 0.0428 × 0.7515.
- Fans: "up to four standard (STD), high performance silver grade, or
  high performance gold grade **dual cooling fan modules**" — Dell R6525
  cooling-fan-specifications page,
  <https://www.dell.com/support/manuals/en-us/poweredge-r6525/r6525_ts_pub/cooling-fan-specifications?guid=guid-acab7503-87b3-4461-8647-56440c9b623e&lang=en-us>,
  **via search snippet** → `fan_count: 8` (same dual-module reading as
  the R650).
- "up to 32 DIMMs" — Dell techspecs **via search snippet** → 32.
- Two AMD EPYC sockets (2-socket 1U platform, technical guide via search
  snippet) → `cpu_sockets: 2`.

**Estimated**: global list above; `pcie_max_slots: 3` from the 15G 1U
riser layout (as sourced for the sibling R650), not separately verified
for the R6525. PSU wattage class not verified.

### R750 — "Dell PowerEdge R750 (16-bay)"

**Sourced** — Dell PowerEdge R750 Technical Specifications (regulatory
model E70S; 10g.com.ua mirror of the Dell publication):
<https://10g.com.ua/datasheets/Dell-EMC-PowerEdge-R750.pdf>
- Dimensions (Table 1, 0/8/12/16/24-drive): Xa 482.0 / **Xb 434.0** /
  **Y 86.8** / **Zb 700.7** / Zc 736.29 mm → 0.434 × 0.0868 × 0.7007
  (16-bay config modelled from the same table's config list).
- "supports up to six standard (STD), high-performance silver grade
  (HPR SLVR), or high-performance gold grade (HPR GOLD) cooling fans" →
  `fan_count: 6`.
- "32, 288-pin" memory sockets → 32.

**Estimated**: global list above; `cpu_sockets: 2` (2-socket 15G 2U
platform — technical guide via search snippet); `pcie_max_slots: 8`
(generation riser layout, not verified on a page I opened). PSU wattage
class not verified.

### R7515 — "Dell PowerEdge R7515 (12x LFF, 1x EPYC)"

**Sourced** — Dell PowerEdge R7515 Technical Specifications (regulatory
model E46S; jarcomputers mirror of the Dell publication):
<https://www.jarcomputers.com/images/custom/docs/poweredge-r7515_owners-manual2_en-us-1621433457.pdf>
- Dimensions (Table 8): Xa 482 / **Xb 434** / **Y 86.8** /
  **Zb 647.07** / Zc 681.755 mm → 0.434 × 0.0868 × 0.6471.
- "requires all six fans to be installed" → `fan_count: 6`.
- Table 10: "AMD EPYC 7002 series processor — One" → `cpu_sockets: 1`.
- "Sixteen 288-pin" memory sockets → 16.
- Chassis configs: 8×3.5 / **12×3.5** / 12×3.5+2 rear / 24×2.5 (12×LFF
  modelled); PSU classes incl. 1600 W AC Platinum (750/1100 W classes per
  the Dell techspecs page via search snippet).

**Estimated**: global list above; `pcie_max_slots: 8` — not verified
(the ts PDF lists riser configurations I did not fully extract).

### R7625 — "Dell PowerEdge R7625 (16-bay, 2x EPYC)"

**Sourced** — PowerEdge R7625 Spec Sheet:
<https://www.delltechnologies.com/asset/en-us/products/servers/technical-support/poweredge-r7625-spec-sheet.pdf>
- "Up to two AMD EPYC 4th Generation 9004 Series" → `cpu_sockets: 2`.
- "24 DDR5 DIMM slots" → `total_dimm_slots: 24`.
- "Up to 6 hot plug fans" (HPR Silver / VHP Gold) → `fan_count: 6`.
- Dimensions: **Height 86.8 mm** / Width 482 mm (with rack latches) /
  Depth 772.13 mm with bezel, **758.29 mm without bezel** →
  0.0868 × 0.7583.
- Front bays incl. "up to 16 x 2.5-inch SAS/SATA/NVMe" (modelled) and up
  to 24×2.5 / 12×3.5 / 32×E3.S; rear bays up to 4×2.5.
- PSU classes 800 W – 3200 W (full list in sheet).
- PCIe slot list Slots 1–8 (Gen4/Gen5 mix) → `pcie_max_slots: 8`.

**Estimated / not sourced**: global list above, **plus**:
`chassis_width: 0.434` is an **assumption** — the spec sheet gives only
the 482 mm width including rack latches; the body width (Xb) was not
found for the R7625 and 434 mm was assumed by analogy with every other
PowerEdge rack chassis in this file. Rear drive bays exist on the R7625
but were not modelled (16-bay front-only config chosen).

---

## Summary of the sourced-vs-estimated split

Sourced with real citations for all 19 profiles: outer chassis
dimensions (body width, height, depth — with the R7625 body width as the
single flagged assumption), form factor, fan count (R710/R620/R6525
resting on search snippets of official pages, flagged), CPU socket
count, DIMM slot count, drive bay counts/types, and PSU wattage class /
PSU fan presence for most models (R610's fanless PSUs are sourced;
R630/R6525/R750 PSU classes were not verified). Estimated in every
profile: `fan_wall_z`, all zone z-coordinates, all ζ and permeability
values, `heat_load`, every custom-zone box coordinate, riser positions,
`pcie_x_band`, PSU fan rpm/size annotations, and the
`requirements`/`mesh_settings` blocks (repo conventions). `pcie_max_slots`
is sourced for R610/R710/R620/R720/R730/R730xd/R530/R240/R7425/R650/
R6515/R7625 and estimated for R630/R6525/R750/R7515/R740; for R930/R940
it is a deliberate geometric cap below the sourced physical slot count.

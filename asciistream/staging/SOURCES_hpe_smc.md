# Sources and honesty ledger — HPE ProLiant + Supermicro profiles

Companion to `staging/profiles_hpe_smc.json`. Ten profiles, keys:
`DL360G7`, `DL360G8`, `DL360G9`, `DL360G10P`, `DL380G7`, `DL380G8`,
`DL380G9`, `DL380G10P`, `1029P`, `6029P`.

## Reading this file

Vendors publish **outer chassis dimensions, fan counts, socket/DIMM/bay/slot
counts, PSU wattages and (sometimes) PSU module dimensions**. They do NOT
publish internal coordinate geometry. Accordingly, for every profile below:

**Sourced (vendor documents, cited per profile):** `chassis_width`,
`chassis_height`, `chassis_length`, `fan_count`, `drive_bay_count`/type,
`cpu_sockets`, `total_dimm_slots`, physical PCIe slot counts backing
`pcie_max_slots`.

**Estimated (engineering judgment, NOT in any vendor document):**
`fan_wall_z`, `drive_zone_z`, `cpu_zone_z`, `pcie_zone_z`, `pcie_x_band`,
riser x-ranges, every PSU `box` coordinate, all `zeta` and `permeability`
values, `baseline_zeta`, `heat_load`, `populated_pcie_slots` (a modeling
choice for a "typically populated" system, not a vendor spec), PSU zone
`fan_rpm`/`fan_size_mm` (except where noted), mesh presets and the
`requirements` block (copied from the repo's existing convention; consistent
with the 10–35 °C operating ambient every one of these vendors specifies).
This extends the estimate convention the repo already documents for its
existing entries.

Fetch notes: `supermicro.com` product pages and the supplied thermal
resource page (https://www.supermicro.com/en/support/resources/thermal)
returned HTTP 403 to my fetch tool, as did `harddrivesdirect.com` and HPE's
`psnow` PDF endpoint (timeouts). Where that happened I used mirrored
QuickSpecs PDFs / user guides and reseller spec sheets, cited below; I did
not silently substitute memory for a failed fetch.

---

## DL360G7 — HPE ProLiant DL360 G7

Source: HP QuickSpecs DA-13598 Worldwide v50 (May 2012), mirror:
https://data.nag.wiki/HP/HP%20ProLiant%20DL360%20G7%20quickspecs.PDF

Sourced facts (quoted from that document):
- Dimensions (H x W x D, with bezel): "1.70 x 16.78 x 27.25 in
  (4.32 x 42.62 x 69.22 cm)" → 0.4262 × 0.0432 × 0.6922 m.
- Fans: "3 fan modules ship standard in 1 processor models / 4 fan modules
  ship standard in 2 processor models. Fan redundancy standard" →
  `fan_count: 4` (2P configuration).
- Storage: "8 SFF SAS/SATA HDD Bays".
- Memory: "Total of 18 DDR3 DIMM slots".
- PCIe: 2 slots ("1-full-length, full-height and 1-low profile") →
  `pcie_max_slots: 2`.
- CPUs: 2 × Xeon 5600, 95 W six-core typical, top bins 130 W ("X5690
  (3.46GHz/6-core/12MB/130W)", "X5687 (3.60GHz/4-core/12MB/130W)").
- PSUs: 460/750/1200 W Common Slot, 1+1 redundant.

Estimated: everything in the "Estimated" list above. `heat_load` 260 W is an
estimate for a loaded 2 × 95 W-CPU system with 8 drives (not a vendor
number). PSU boxes use the HP Common Slot module footprint (~75 mm wide,
~185 mm deep) — that footprint itself is an estimate here, not quoted from
the QuickSpecs. Both PSU bays sit side-by-side at one rear corner (position
estimated from rear-panel photos in the QuickSpecs overview, coordinates
mine).

## DL360G8 — HPE ProLiant DL360p Gen8

Source: HP QuickSpecs DA-14211 Worldwide v64 (Sept 2015), mirror:
https://www.hardwarewartung.com/quickspecs/hpe-proliant-dl360p-gen8.pdf

Sourced facts:
- Dimensions (8/10 SFF): "1.7 x 17.1 x 27.5 inches (4.32 x 43.47 x 69.85
  cm)" → 0.4347 × 0.0432 × 0.6985 m.
- Fans: "System Fans - 6 (2 additional fans included with second
  processor)"; 2P pre-configured models list "8 Hot Plug Redundant Fan
  Modules" → `fan_count: 8` (2P).
- Storage: "Standard: 8 SFF HDD Bays".
- Memory: "24 (12 DIMM slots per processor / 4 channels per processor / 3
  DIMMs per channel)".
- PCIe: "2 standard (1-FH/HL, 1-LP) PCIe 3.0" → `pcie_max_slots: 2`.
- CPUs: 2 × E5-2600/v2, up to 135 W ("E5-2690 … 135W").
- PSUs: 460/750 W Common Slot hot-plug.

Estimated: standard list. `heat_load` 280 W estimated. PSU footprint and
coordinates estimated as for DL360G7.

## DL360G9 — HPE ProLiant DL360 Gen9

Source: HPE QuickSpecs c04346229 (Nov 2016 revision), mirror:
https://www.dve-x.com/fileadmin/user_upload/produkte/Hewlett_Packard/PDFs/HPE_ProLiant_DL360_Gen9_QuickSpecs_Nov2016.pdf

Sourced facts:
- Dimensions (8/10 SFF): "1.7 x 17.1 x 27.5 inches (4.32 x 43.47 x 69.85
  cm)" — same envelope as Gen8.
- Fans: "1P model … 5 fans / 2P model … 7 fans", "7 standard hot plug fans,
  redundant" on 2P models → `fan_count: 7` (2P). High Performance Fan Kit
  766201-B21 exists for extended ambient/NVMe.
- Storage: 8 SFF standard.
- Memory: "DIMM Slots Available 24 (12 DIMM slots per processor, 4 channels
  per processor, 3 DIMMs per channel)".
- PCIe: up to 3 slots (2 standard + 1 via optional secondary riser) →
  `pcie_max_slots: 3`.
- CPUs: 2 × E5-2600 v3/v4, up to 145 W top bins.

Estimated: standard list. `heat_load` 300 W estimated.

## DL360G10P — HPE ProLiant DL360 Gen10 Plus

Source: HPE QuickSpecs a50002559enw, mirror:
https://www.fbcinc.com/source/virtualhall_images/NLIT_June_21/Holmans/DL360_Gen_10_(1).pdf
(HPE canonical page, direct fetch timed out for me:
https://www.hpe.com/psnow/doc/a50002559enw)

Sourced facts:
- Dimensions (SFF): "4.29 x 43.46 x 74.19 cm / 1.69 x 17.11 x 29.21 in" →
  0.4346 × 0.0429 × 0.7419 m. NOTE: measurably deeper than Gen9 (74.19 vs
  69.85 cm) — a real generational difference reflected in
  `chassis_length`.
- Fans: "will support up to 7 fans with fan redundancy built in"; the
  quoted SFF minimum weight config carries "five fans", the maximum "seven
  fans"; "Single rotor hot plug fans by default" → `fan_count: 7` (2P).
- Storage: "8 SFF with options for additional 2 SFF drive bays".
- Memory: "DIMM Slots Available 32 — 16 DIMM slots per processor, 8
  channels per processor, 2 DIMMs per channel".
- PCIe: 3 × PCIe 4.0 slots (2 on primary riser CPU1, 1 on secondary CPU2)
  → `pcie_max_slots: 3`.
- CPUs: "3rd Generation Intel Xeon Scalable processors: 16 to 40 cores …
  165W to 270W TDP" (performance models; Platinum 8380 = 270 W).
- PSUs: HPE Flexible Slot hot-plug.

Estimated: standard list. `heat_load` 420 W is an estimate tracking the much
higher CPU TDP class (2 × ~200 W typical) — not a vendor number.

## DL380G7 — HPE ProLiant DL380 G7

Source: HP ProLiant DL380 G7 Server User Guide (Part No. 594816-004),
mirror: https://www.istoragenetworks.com/servermanuals/dl380g7_userguide.pdf
Supplemental: HP QuickSpecs DL380 G7 (via search snippets; PDF mirrors of
the QuickSpecs 403'd for me), and
https://www.mrmemory.co.uk/downloads/memory-configurations/123905.pdf for
the 18-DIMM count.

Sourced facts:
- Mechanical specifications (user guide): "Height 8.59 cm (3.38 in),
  Depth 66.07 cm (26.01 in), Width 44.54 cm (17.54 in)" →
  0.4454 × 0.0859 × 0.6607 m. NOTE: the QuickSpecs quote 27.25 in depth
  (with bezel); I used the user guide's self-consistent mechanical table
  (without bezel). Either way the G7 is genuinely SHALLOWER than the
  Gen8/Gen9 DL380.
- Fans (user guide "Hot-plug fans"): 6 fan bays; "1 processor: Fan Fan Fan
  Fan / Fan blank Fan blank; 2 processors: Fan × 6"; "For a dual-processor
  configuration, six fans are required for redundancy" → `fan_count: 6`
  (2P).
- PCIe (user guide riser slot definitions): standard PCIe2 riser = 3 slots
  primary + 3 slots via optional secondary riser → `pcie_max_slots: 6`.
- Memory: 18 DIMM slots (9 per CPU).
- Storage: 8 SFF standard (up to 16).
- CPUs: 2 × Xeon 5600, up to 130 W. PSUs: 460/750/1200 W (user guide power
  specification tables).

Estimated: standard list. `heat_load` 280 W estimated. PSU stack modeled at
one rear corner, two modules stacked vertically (2U layout), coordinates and
Common Slot footprint estimated.

## DL380G8 — HPE ProLiant DL380p Gen8

Source: HP QuickSpecs c04123238 / DA-14212 Worldwide v72 (Sept 2015),
mirror: https://mahanshabake.com/wp-content/uploads/2024/08/HP-ProLiant-DL380p-Gen8.pdf

Sourced facts:
- Dimensions (SFF): "3.44 x 17.54 x 27.50 in (8.73 x 44.55 x 69.85 cm)" →
  0.4455 × 0.0873 × 0.6985 m. NOTE: deeper than both the G7 (66.07 cm) and
  the Gen9 (67.94 cm).
- Fans: "2P Models have (6) (N+1 redundancy standard); 1P Models have (4)"
  → `fan_count: 6` (2P).
- Storage: 8 SFF standard (8LFF/12LFF/16SFF/25SFF CTO variants exist).
- Memory: 24 DIMM slots (12 per processor).
- PCIe: primary riser standard 3 slots (x16 FL/FH, x8 HL/FH, x4 HL/FH),
  second optional riser (requires CPU2) for 3 more → `pcie_max_slots: 6`.
- CPUs: 2 × E5-2600/v2, up to 135 W.

Estimated: standard list. `heat_load` 310 W estimated.

## DL380G9 — HPE ProLiant DL380 Gen9

Source: HPE QuickSpecs (Nov 2016 revision), mirror:
https://www.dve-x.com/fileadmin/user_upload/produkte/Hewlett_Packard/PDFs/HPE_ProLiant_DL380_Gen9_QuickSpecs_Nov2016.pdf

Sourced facts:
- Dimensions (SFF): "3.44 x 17.54 x 26.75 in (8.73 x 44.55 x 67.94 cm)" →
  0.4455 × 0.0873 × 0.6794 m. NOTE: ~1.9 cm shallower than the Gen8 — a
  real generational difference.
- Fans: "2P model … 6 fans" redundant; "1P models typically ship with 4
  standard fans"; "12LFF and 24SFF models ship with 6 High Performance fans
  as standard" → `fan_count: 6` (2P).
- Storage: 8 SFF standard.
- Memory: 24 DIMM slots.
- PCIe: up to 6 slots via two 3-slot risers → `pcie_max_slots: 6`.
- CPUs: 2 × E5-2600 v3/v4, up to 145 W.

Estimated: standard list. `heat_load` 340 W estimated.

## DL380G10P — HPE ProLiant DL380 Gen10 Plus

Source: HPE QuickSpecs a50002553enw, mirror:
https://media.dustin.eu/media/d200001001641613/proliant-dl380-gen10-server-rack-2u-intel-xeon-gold-5315y-32-ghz-32-gb-ddr4-sdram-800-w-productdatasheetbrochure.pdf
(HPE canonical: https://www.hpe.com/psnow/doc/a50002553enw.pdf — direct
fetch timed out for me.)

Sourced facts:
- Dimensions (SFF): "8.75 x 44.54 x 71. cm / 3.44 x 17.54 x 28 in" →
  0.4454 × 0.0875 × 0.710 m. NOTE: deeper than Gen9 (67.94 cm).
- Fans: "On SFF Chassis only, 1P models ship with 4 standard fans. If a
  second processor … is added, then Qty 1 Standard Fan Kit (P37042-B21)
  must be selected, which includes the additional 2 standard fans" → 6 fans
  2P → `fan_count: 6`. Maximum Performance Fan Kit (P14608-B21) optional.
- Storage: 8 SFF standard ("Box 3 - 8 SFF Drive Cage Bay"; up to 24 SFF
  front + mid/rear options to 38 SFF).
- Memory: "DIMM Slots Available 32 — 16 DIMM slots per processor".
- PCIe: PCIe 4.0, up to 8 slots via risers → `pcie_max_slots: 8`.
- CPUs: 3rd Gen Xeon Scalable up to 270 W (Platinum 8380 quoted at 270 W).

Estimated: standard list. `heat_load` 460 W estimated (tracks 270 W-class
sockets; not a vendor number).

## 1029P — Supermicro SuperServer 1029P-WTR

Sources:
- Supermicro spec sheet for the sibling SKU SYS-1029P-WTRT (same
  CSE-116AC2-R706WB2 chassis; the -WTRT differs in LAN only), mirrored by
  reseller BSI:
  https://bsicomputer.com/prodimg/16539/supermicro-1029p-wtrt-1u-rackmount-server-specifications.pdf
- 1029P-WTR-specific reseller listing: https://mitxpc.com/products/1029p-wtr
- Canonical (403'd to my fetch tool, listed for reference):
  https://www.supermicro.com/en/products/system/1U/1029/SYS-1029P-WTR.php

The requester wrote "1029P-WT"; I used the shipping redundant-PSU SKU
**SYS-1029P-WTR** (the -WT is the single-PSU variant), as sourced above.

Sourced facts:
- Dimensions: "Width 17.2" (437mm), Height 1.7" (43mm), Depth 23.5"
  (597mm)" → 0.437 × 0.043 × 0.597 m (notably shallow for a 1U).
- Chassis: CSE-116AC2-R706WB2.
- Fans: "4 Counter-rotating 4cm PWM fans" (parts list: FAN-0101L4, a
  40 × 56 mm dual-rotor unit) → `fan_count: 4`.
- Storage: 8 × 2.5" hot-swap SAS/SATA (MITXPC, WTR); the WTRT sheet's
  backplane supports 10 (8 + 2 NVMe/SATA hybrid). I modeled the 8-bay WTR
  configuration.
- CPUs: dual Socket P LGA3647, "Support CPU TDP 70-165W with IVR".
- Memory: 12 DIMM slots.
- PCIe (WIO): 2 × PCIe 3.0 x16 FHHL + 1 × x8 LP + 1 × x16 AOM (risers
  RSC-R1UW-2E16 / RSC-R1UW-E8R in the parts list) → `pcie_max_slots: 3`
  (the three card slots; AOM not modeled).
- PSUs: 750 W redundant PWS-706P-1R; module dimensions "(W x H x L)
  54.5 x 40.25 x 320 mm" — the PSU cross-section (54.5 mm wide × 40.25 mm
  high, two modules side-by-side at the rear right) in my PSU boxes is
  taken from this sourced module size. The z-extent of the PSU boxes is
  truncated to 142 mm (the sourced module is 320 mm long) so the zone stays
  clear of the auto-generated CPU2 heatsink — a deliberate modeling
  compromise, documented here.

Estimated: standard list (fan-wall position, all zone z-extents, zeta/
permeability, PSU z-extent as noted). `heat_load` 320 W estimated for a
2 × 125–165 W configuration. PSU `fan_rpm` 16000 estimated.

## 6029P — Supermicro SuperServer 6029P-WTR

Sources (two independent resellers agree on all values used):
- https://www.bsicomputer.com/products/6029p-wtr-16624
- https://mitxpc.com/products/6029p-wtr
- Canonical (403'd to my fetch tool, listed for reference):
  https://www.supermicro.com/en/products/system/2U/6029/SYS-6029P-WTR.php

Sourced facts:
- Dimensions: "648 x 437 x 89mm (25.5" x 17.2" x 3.5")" →
  0.437 × 0.089 × 0.648 m.
- Fans: "3x 80x38mm 9.4K RPM Hot-Swappable Cooling Fans" (BSI: "3x 80mm
  Heavy Duty PWM Fans") → `fan_count: 3`.
- Storage: "8x 3.5" Hot-Swap SAS/SATA Drive bays with SGPIO" plus 2 fixed
  internal 3.5" bays — only the 8 front hot-swap bays are modeled; the
  2 fixed internal bays are NOT modeled (documented gap).
- CPUs: dual Socket P LGA3647, max TDP 205 W (MITXPC; Supermicro's page
  states 70–205 W).
- Memory: 12 DIMM slots.
- PCIe (WIO): 4 × FHHL + 2 × LP + 1 × x16 AOM. Reseller discrepancy: BSI
  lists the 4 FHHL slots as x8, MITXPC as x16; electrical width does not
  affect this model. `pcie_max_slots: 6` (the six card slots).
- PSUs: 1000 W redundant, Titanium.

Estimated: standard list. PSU module cross-section assumed similar to the
1029P's sourced 54.5 × ~40 mm module (stacked vertically in 2U), coordinates
mine; z-extent truncated to 143 mm as for the 1029P. `heat_load` 400 W
estimated for a 2 × 165–205 W configuration. PSU `fan_rpm` 16000 estimated.

---

## Real generational differences captured in the data

- **Depth changes**: DL380 G7 660.7 mm → Gen8 698.5 mm → Gen9 679.4 mm →
  Gen10 Plus 710 mm; DL360 G7/Gen8/Gen9 692.2/698.5/698.5 mm → Gen10 Plus
  741.9 mm. All sourced; `chassis_length` differs accordingly.
- **Width**: DL360 G7 is narrower (426.2 mm) than Gen8+ (434.7 mm).
- **Fan modules**: DL360 fan count is genuinely non-monotonic across
  generations: G7 = 4 modules, Gen8 = 8, Gen9 = 7, Gen10 Plus = 7 (all
  sourced, 2P configurations). DL380 = 6 across all four generations
  (sourced), but the G7 uses non-Common fan modules with mandatory blank
  rules, Gen9 introduced High Performance fan variants, and Gen10 Plus
  moved to a standard/max-performance kit split — reflected only in
  `fan_count` plus these notes, since module part numbers carry no
  geometry in this schema.
- **Memory**: 18 DIMM (G7) → 24 (Gen8/Gen9) → 32 (Gen10 Plus), sourced;
  changes the auto-generated DIMM bank widths.
- **Heat class**: Xeon 5600 (95–130 W) → E5 v1/v2 (to 135 W) → E5 v3/v4
  (to 145 W) → Ice Lake-SP (165–270 W); TDP ceilings sourced, the
  `heat_load` totals built on them are estimates.

## What I could NOT source (global summary)

- Any internal coordinate: fan-wall z-position, zone z-extents, riser and
  PSU box coordinates. All estimated.
- Any impedance (`zeta`, `permeability`) or `baseline_zeta`. All estimated.
- Total system `heat_load` under load. Estimated from CPU TDP class +
  DIMM/drive count.
- HPE Common Slot / Flex Slot PSU module external dimensions (widely known
  to be ~73.5 × 39.5 × 185 mm for Common Slot, but I found no citable
  vendor page during this session) — used as an estimate and flagged.
- Supermicro's official thermal resource page (403) and supermicro.com
  product pages (403): substituted the mirrored spec sheet + two
  independent resellers, cited above.
- 6029P-WTR PSU module dimensions (assumed similar to the 1029P's sourced
  PWS-706P-1R cross-section).
- DL380 G7 QuickSpecs PDF itself (mirrors 403'd); used the official user
  guide instead, which is arguably the better mechanical source.

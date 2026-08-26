#!/usr/bin/env python3
"""
eda2eagle.py - Universal EDA netlist to Eagle CAD XML schematic converter.
Supports: KiCad (.net), SPICE (.cir), PADS ASCII (.asc), CadStar-style (.txt)
USAGE:
    python eda2eagle.py input.net output.sch [clearance_mm]
    OR double-click for GUI requester (Windows 10 Pro)
"""
import sys
import re
import math
import os

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

# ---------------------------------------------------------------------------
# Layout and geometry constants
# ---------------------------------------------------------------------------
PIN_PITCH = 2.54
STUB_LEN = 5.08
LABEL_SIZE = 1.27
CHAR_WIDTH_FACTOR = 0.85

# ---------------------------------------------------------------------------
# Format detection based on structural signatures
# ---------------------------------------------------------------------------
def detect_format(content):
    """Detect netlist format from file content, not extension."""
    if "(export" in content and "version" in content:
        return "kicad"
    if "*PADS-LIBRARY*" in content or "*PADS-PCB*" in content:
        return "pads"
    if "NETLIST" in content and "$PACKAGES" in content:
        return "cadstar"
    if re.search(r'^[A-Z]\w*\s+\w+\s+\w+', content, re.MULTILINE):
        return "spice"
    return "unknown"

# ---------------------------------------------------------------------------
# S-expression parser (KiCad format)
# ---------------------------------------------------------------------------
def tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif c == '(':
            tokens.append('(')
            i += 1
        elif c == ')':
            tokens.append(')')
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j+1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append(('str', ''.join(buf)))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()':
                j += 1
            tokens.append(('atom', text[i:j]))
            i = j
    return tokens

def parse_tokens(tokens):
    pos = [0]
    def parse_expr():
        tok = tokens[pos[0]]
        if tok == '(':
            pos[0] += 1
            items = []
            while tokens[pos[0]] != ')':
                items.append(parse_expr())
            pos[0] += 1
            return items
        else:
            pos[0] += 1
            return tok[1]
    return parse_expr()

def parse_sexpr(text):
    return parse_tokens(tokenize(text))

def find_all(tree, tag):
    results = []
    if isinstance(tree, list):
        if tree and tree[0] == tag:
            results.append(tree)
        for item in tree:
            results.extend(find_all(item, tag))
    return results

def find_first(tree, tag):
    if isinstance(tree, list):
        if tree and tree[0] == tag:
            return tree
        for item in tree:
            r = find_first(item, tag)
            if r is not None:
                return r
    return None

def get_value(node, tag, default=None):
    sub = find_first(node, tag)
    if sub and len(sub) > 1 and not isinstance(sub[1], list):
        return sub[1]
    return default

def extract_components_kicad(tree):
    comps = {}
    for c in find_all(tree, 'comp'):
        ref = get_value(c, 'ref')
        if not ref:
            continue
        comps[ref] = {
            'value': get_value(c, 'value', ''),
            'footprint': get_value(c, 'footprint', ''),
        }
    return comps

def extract_nets_kicad(tree):
    nets = []
    for net_node in find_all(tree, 'net'):
        name = get_value(net_node, 'name', '')
        pins = []
        for node in find_all(net_node, 'node'):
            ref = get_value(node, 'ref')
            pin = get_value(node, 'pin')
            if ref and pin:
                pins.append((ref, pin))
        if pins:
            nets.append((name, pins))
    return nets

# ---------------------------------------------------------------------------
# SPICE parser
# ---------------------------------------------------------------------------
def parse_spice(content):
    components = {}
    nets = {}
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('.'):
            continue
        tokens = line.split()
        if len(tokens) == 4:
            ref = tokens[0]
            node1, node2 = tokens[1], tokens[2]
            components[ref] = {'value': tokens[3], 'footprint': ''}
            for node in [node1, node2]:
                if node not in nets:
                    nets[node] = []
                nets[node].append((ref, '1' if node == node1 else '2'))
        elif len(tokens) == 2 and '__' in tokens[0]:
            continue
    return components, [(name, pins) for name, pins in nets.items()]

# ---------------------------------------------------------------------------
# PADS ASCII parser
# ---------------------------------------------------------------------------
def parse_pads(content):
    components = {}
    nets = []
    in_part_section = False
    for line in content.split('\n'):
        line = line.strip()
        if line == '*PART*':
            in_part_section = True
            continue
        if line.startswith('*') and in_part_section:
            in_part_section = False
            continue
        if in_part_section and line:
            tokens = line.split()
            if len(tokens) >= 2:
                ref = tokens[0]
                footprint = tokens[1]
                value = tokens[2] if len(tokens) > 2 else ''
                components[ref] = {'value': value, 'footprint': footprint}
    
    current_net = None
    current_pins = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('*SIGNAL*'):
            if current_net and current_pins:
                nets.append((current_net, current_pins))
            current_net = line.replace('*SIGNAL*', '').strip()
            current_pins = []
        elif current_net and line and not line.startswith('*'):
            for token in line.split():
                if '.' in token:
                    ref, pin = token.split('.', 1)
                    current_pins.append((ref, pin))
    if current_net and current_pins:
        nets.append((current_net, current_pins))
    return components, nets

# ---------------------------------------------------------------------------
# CadStar parser
# ---------------------------------------------------------------------------
def parse_cadstar(content):
    components = {}
    nets = []
    in_packages = False
    for line in content.split('\n'):
        line = line.strip()
        if line == '$PACKAGES':
            in_packages = True
            continue
        if line.startswith('$') and in_packages:
            in_packages = False
            continue
        if in_packages and line:
            tokens = line.split(',')
            if len(tokens) >= 2:
                ref = tokens[0].strip()
                footprint = tokens[1].strip()
                value = tokens[2].strip() if len(tokens) > 2 else ''
                components[ref] = {'value': value, 'footprint': footprint}
    
    in_nets = False
    current_net = None
    current_pins = []
    for line in content.split('\n'):
        line = line.strip()
        if line == '$NETS':
            in_nets = True
            continue
        if line.startswith('$') and in_nets:
            if current_net and current_pins:
                nets.append((current_net, current_pins))
            in_nets = False
            continue
        if in_nets and line:
            if line.startswith('"'):
                if current_net and current_pins:
                    nets.append((current_net, current_pins))
                current_net = line.strip('"')
                current_pins = []
            elif current_net:
                for token in line.split(','):
                    token = token.strip()
                    if '.' in token:
                        ref, pin = token.split('.', 1)
                        current_pins.append((ref, pin))
    if current_net and current_pins:
        nets.append((current_net, current_pins))
    return components, nets

# ---------------------------------------------------------------------------
# Net name sanitization
# ---------------------------------------------------------------------------
def sanitize_net_name(name):
    name = name.replace('~{', 'N_').replace('}', '')
    name = re.sub(r'[^A-Za-z0-9_+\-.]', '_', name)
    if not name:
        name = 'NET'
    return name

# ---------------------------------------------------------------------------
# Symbol generation
# ---------------------------------------------------------------------------
def box_wires(x1, y1, x2, y2):
    w = 0.4064
    return (f'<wire x1="{x1}" y1="{y1}" x2="{x2}" y2="{y1}" width="{w}" layer="94"/>'
            f'<wire x1="{x2}" y1="{y1}" x2="{x2}" y2="{y2}" width="{w}" layer="94"/>'
            f'<wire x1="{x2}" y1="{y2}" x2="{x1}" y2="{y2}" width="{w}" layer="94"/>'
            f'<wire x1="{x1}" y1="{y2}" x2="{x1}" y2="{y1}" width="{w}" layer="94"/>')

def make_symbol(ref, pin_numbers):
    n = len(pin_numbers)
    left_n = (n + 1) // 2
    right_n = n - left_n
    half_h = max(left_n, right_n, 1) * PIN_PITCH / 2 + PIN_PITCH / 2
    half_w = 6.35
    body = box_wires(-half_w, -half_h, half_w, half_h)
    
    pins_xml = []
    for i, pn in enumerate(pin_numbers[:left_n]):
        y = half_h - PIN_PITCH/2 - i * PIN_PITCH
        pins_xml.append(f'<pin name="P{pn}" x="{-(half_w+PIN_PITCH)}" y="{y}" length="short" direction="io"/>')
    for i, pn in enumerate(pin_numbers[left_n:]):
        y = half_h - PIN_PITCH/2 - i * PIN_PITCH
        pins_xml.append(f'<pin name="P{pn}" x="{half_w+PIN_PITCH}" y="{y}" length="short" direction="io" rot="R180"/>')
    
    name_y = half_h + 2.0
    val_y = -half_h - 3.5
    text_xml = (f'<text x="{-half_w}" y="{name_y}" size="1.4" layer="95">&gt;NAME</text>'
                f'<text x="{-half_w}" y="{val_y}" size="1.27" layer="96">&gt;VALUE</text>')
    return body + ''.join(pins_xml) + text_xml, half_w, half_h

# ---------------------------------------------------------------------------
# Auto-placement with spiral and bounding-box collision
# ---------------------------------------------------------------------------
def compute_auto_placement(refs, nets_raw, extents, max_fanout=8, spiral_step=3.0, 
                           spiral_growth=2.2, margin=6.0, corner_gap=80.0, corner_row_width=350.0):
    if not HAS_NETWORKX:
        return None
    
    G = nx.Graph()
    G.add_nodes_from(refs)
    for name, pins in nets_raw:
        distinct_refs = sorted(set(r for r, p in pins))
        if len(distinct_refs) < 2 or len(pins) > max_fanout:
            continue
        for i in range(len(distinct_refs)):
            for j in range(i + 1, len(distinct_refs)):
                a, b = distinct_refs[i], distinct_refs[j]
                w = G[a][b]['weight'] + 1 if G.has_edge(a, b) else 1
                G.add_edge(a, b, weight=w)
    
    degrees = dict(G.degree(weight='weight'))
    main_refs = [r for r in refs if degrees.get(r, 0) > 0]
    bypass_refs = [r for r in refs if degrees.get(r, 0) == 0]
    main_refs.sort(key=lambda r: -degrees.get(r, 0))
    
    def box_of(ref, cx, cy):
        left_ext, right_ext, half_h = extents[ref]
        return (cx - left_ext - margin/2, cy - half_h - margin/2,
                cx + right_ext + margin/2, cy + half_h + margin/2)
    
    def overlaps(b1, b2):
        return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])
    
    placed_boxes = []
    coords = {}
    
    for ref in main_refs:
        theta = 0.0
        found = False
        while not found:
            r = spiral_step * theta
            cx = r * math.cos(theta)
            cy = r * math.sin(theta)
            candidate = box_of(ref, cx, cy)
            if not any(overlaps(candidate, pb) for pb in placed_boxes):
                coords[ref] = (round(cx, 2), round(cy, 2))
                placed_boxes.append(candidate)
                found = True
            theta += spiral_growth / max(r, 1.0)
    
    if placed_boxes:
        max_x = max(b[2] for b in placed_boxes)
        min_y = min(b[1] for b in placed_boxes)
    else:
        max_x, min_y = 0.0, 0.0
    
    corner_x0 = max_x + corner_gap
    corner_y0 = min_y
    row_width_budget = corner_row_width
    cursor_x = corner_x0
    cursor_y = corner_y0
    row_max_h = 0.0
    row_start_x = corner_x0
    
    for ref in bypass_refs:
        left_ext, right_ext, half_h = extents[ref]
        width = left_ext + right_ext + margin
        height = 2 * half_h + margin
        
        if cursor_x + width > row_start_x + row_width_budget and cursor_x > row_start_x:
            cursor_x = row_start_x
            cursor_y -= row_max_h
            row_max_h = 0.0
        
        cx = cursor_x + left_ext
        coords[ref] = (round(cx, 2), round(cursor_y - half_h, 2))
        cursor_x += width
        row_max_h = max(row_max_h, height)
    
    return coords

# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------
LAYERS = """
<layer number="1" name="Top" color="4" fill="1" visible="no" active="no"/>
<layer number="16" name="Bottom" color="1" fill="1" visible="no" active="no"/>
<layer number="17" name="Pads" color="2" fill="1" visible="no" active="no"/>
<layer number="18" name="Vias" color="2" fill="1" visible="no" active="no"/>
<layer number="19" name="Unrouted" color="6" fill="1" visible="no" active="no"/>
<layer number="20" name="Dimension" color="15" fill="1" visible="no" active="no"/>
<layer number="21" name="tPlace" color="7" fill="1" visible="no" active="no"/>
<layer number="22" name="bPlace" color="7" fill="1" visible="no" active="no"/>
<layer number="25" name="tNames" color="7" fill="1" visible="no" active="no"/>
<layer number="26" name="bNames" color="7" fill="1" visible="no" active="no"/>
<layer number="27" name="tValues" color="7" fill="1" visible="no" active="no"/>
<layer number="28" name="bValues" color="7" fill="1" visible="no" active="no"/>
<layer number="35" name="tGlue" color="7" fill="1" visible="no" active="no"/>
<layer number="36" name="bGlue" color="7" fill="1" visible="no" active="no"/>
<layer number="39" name="tKeepout" color="4" fill="11" visible="no" active="no"/>
<layer number="40" name="bKeepout" color="1" fill="11" visible="no" active="no"/>
<layer number="41" name="tRestrict" color="4" fill="10" visible="no" active="no"/>
<layer number="42" name="bRestrict" color="1" fill="10" visible="no" active="no"/>
<layer number="43" name="vRestrict" color="2" fill="10" visible="no" active="no"/>
<layer number="44" name="Drills" color="7" fill="1" visible="no" active="no"/>
<layer number="45" name="Holes" color="7" fill="1" visible="no" active="no"/>
<layer number="46" name="Milling" color="3" fill="1" visible="no" active="no"/>
<layer number="47" name="Measures" color="7" fill="1" visible="no" active="no"/>
<layer number="48" name="Document" color="7" fill="1" visible="no" active="no"/>
<layer number="49" name="Reference" color="7" fill="1" visible="no" active="no"/>
<layer number="51" name="tDocu" color="7" fill="1" visible="no" active="no"/>
<layer number="52" name="bDocu" color="7" fill="1" visible="no" active="no"/>
<layer number="91" name="Nets" color="2" fill="1" visible="yes" active="yes"/>
<layer number="92" name="Busses" color="1" fill="1" visible="no" active="yes"/>
<layer number="93" name="Pins" color="2" fill="1" visible="no" active="yes"/>
<layer number="94" name="Symbols" color="4" fill="1" visible="yes" active="yes"/>
<layer number="95" name="Names" color="7" fill="1" visible="yes" active="yes"/>
<layer number="96" name="Values" color="7" fill="1" visible="yes" active="yes"/>
<layer number="97" name="Info" color="7" fill="1" visible="yes" active="yes"/>
<layer number="98" name="Guide" color="6" fill="1" visible="yes" active="yes"/>
""".strip()

# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------
def prepare_data(input_path):
    with open(input_path, encoding='utf-8') as f:
        content = f.read()
    
    fmt = detect_format(content)
    if fmt == "kicad":
        tree = parse_sexpr(content)
        components = extract_components_kicad(tree)
        nets_raw = extract_nets_kicad(tree)
    elif fmt == "spice":
        components, nets_raw = parse_spice(content)
    elif fmt == "pads":
        components, nets_raw = parse_pads(content)
    elif fmt == "cadstar":
        components, nets_raw = parse_cadstar(content)
    else:
        raise ValueError(f"Unknown netlist format: {fmt}")
    
    used_pins = {}
    for name, pins in nets_raw:
        for (ref, pin) in pins:
            used_pins.setdefault(ref, set()).add(pin)
    
    refs = sorted(used_pins.keys())
    pin_numbers_by_ref = {ref: sorted(used_pins[ref], key=lambda p: (len(p), p)) for ref in refs}
    
    pin_to_net = {}
    for name, pins in nets_raw:
        clean = sanitize_net_name(name)
        for (ref, pin) in pins:
            pin_to_net[(ref, pin)] = clean
    
    return {
        'format': fmt,
        'components': components,
        'nets_raw': nets_raw,
        'refs': refs,
        'pin_numbers_by_ref': pin_numbers_by_ref,
        'pin_to_net': pin_to_net,
    }

# ---------------------------------------------------------------------------
# Layout computation
# ---------------------------------------------------------------------------
def compute_layout(data, clearance=8.0):
    refs = data['refs']
    pin_numbers_by_ref = data['pin_numbers_by_ref']
    pin_to_net = data['pin_to_net']
    nets_raw = data['nets_raw']
    
    half_sizes = {}
    for ref in refs:
        n = len(pin_numbers_by_ref[ref])
        left_n = (n + 1) // 2
        right_n = n - left_n
        half_h = max(left_n, right_n, 1) * PIN_PITCH / 2 + PIN_PITCH / 2
        half_sizes[ref] = (6.35, half_h)
    
    def label_width(text):
        return len(text) * LABEL_SIZE * CHAR_WIDTH_FACTOR + clearance
    
    extents = {}
    for ref in refs:
        pin_numbers = pin_numbers_by_ref[ref]
        n = len(pin_numbers)
        left_n = (n + 1) // 2
        left_pins = pin_numbers[:left_n]
        right_pins = pin_numbers[left_n:]
        half_w, half_h = half_sizes[ref]
        
        left_label_w = max([label_width(pin_to_net.get((ref, p), '')) for p in left_pins], default=0.0)
        right_label_w = max([label_width(pin_to_net.get((ref, p), '')) for p in right_pins], default=0.0)
        
        left_extent = half_w + STUB_LEN + left_label_w
        right_extent = half_w + STUB_LEN + right_label_w
        extents[ref] = (left_extent, right_extent, half_h + clearance/2)
    
    placement = compute_auto_placement(refs, nets_raw, extents, margin=clearance)
    return extents, placement, half_sizes

# ---------------------------------------------------------------------------
# Preview generation
# ---------------------------------------------------------------------------
def render_preview(refs, extents, placement, png_path, clearance):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    
    fig, ax = plt.subplots(figsize=(16, 16))
    for ref in refs:
        if ref not in placement:
            continue
        cx, cy = placement[ref]
        left_ext, right_ext, half_h = extents[ref]
        rect = patches.Rectangle((cx - left_ext, cy - half_h),
                                 left_ext + right_ext, 2 * half_h,
                                 linewidth=0.5, edgecolor='steelblue',
                                 facecolor='lightsteelblue', alpha=0.4)
        ax.add_patch(rect)
        ax.text(cx, cy, ref, fontsize=5, ha='center', va='center')
    
    ax.set_title(f'Preview placement - clearance={clearance}mm - {len(refs)} components')
    ax.set_aspect('equal')
    ax.autoscale_view()
    plt.savefig(png_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

# ---------------------------------------------------------------------------
# XML generation
# ---------------------------------------------------------------------------
def generate_sch(data, extents, placement, output_path, max_cols=15, cell_w=45, cell_h=45):
    components = data['components']
    nets_raw = data['nets_raw']
    refs = data['refs']
    pin_numbers_by_ref = data['pin_numbers_by_ref']
    use_grid_fallback = placement is None
    
    symbols_xml = []
    pin_abs = {}
    pin_local = {}
    instances_xml = []
    parts_xml = []
    
    for idx, ref in enumerate(refs):
        pin_numbers = pin_numbers_by_ref[ref]
        sym_name = f'SYM_{ref}'
        sym_body, half_w, half_h = make_symbol(ref, pin_numbers)
        symbols_xml.append(f'<symbol name="{sym_name}">{sym_body}</symbol>')
        
        if use_grid_fallback:
            col = idx % max_cols
            row = idx // max_cols
            gx = col * cell_w
            gy = -row * cell_h
        else:
            gx, gy = placement[ref]
        
        value = components.get(ref, {}).get('value', '')
        instances_xml.append(
            f'<instance part="{ref}" gate="A" x="{gx}" y="{gy}">'
            f'<attribute name="NAME" x="{gx-half_w}" y="{gy+half_h+2.0}" size="1.4" layer="95"/>'
            f'<attribute name="VALUE" x="{gx-half_w}" y="{gy-half_h-3.5}" size="1.27" layer="96"/>'
            f'</instance>'
        )
        
        safe_value = (value or ref).replace('"', "'")
        parts_xml.append(f'<part name="{ref}" library="EDA_IMPORT" deviceset="DS_{ref}" device="" value="{safe_value}"/>')
        
        n = len(pin_numbers)
        left_n = (n + 1) // 2
        for i, pn in enumerate(pin_numbers[:left_n]):
            y = half_h - PIN_PITCH/2 - i * PIN_PITCH
            x = -(half_w + PIN_PITCH)
            pin_abs[(ref, pn)] = (gx + x, gy + y)
            pin_local[(ref, pn)] = (x, y)
        for i, pn in enumerate(pin_numbers[left_n:]):
            y = half_h - PIN_PITCH/2 - i * PIN_PITCH
            x = half_w + PIN_PITCH
            pin_abs[(ref, pn)] = (gx + x, gy + y)
            pin_local[(ref, pn)] = (x, y)
    
    devicesets_xml = ''.join(
        f'<deviceset name="DS_{ref}" prefix="{re.match(r"[A-Za-z]+", ref).group() if re.match(r"[A-Za-z]+", ref) else "X"}">'
        f'<gates><gate name="A" symbol="SYM_{ref}" x="0" y="0"/></gates>'
        f'<devices><device name=""><technologies><technology name=""/></technologies></device></devices>'
        f'</deviceset>'
        for ref in refs
    )
    
    def stub_endpoint(ref, pin):
        ax, ay = pin_abs[(ref, pin)]
        lx, ly = pin_local[(ref, pin)]
        if lx > 0:
            return ax, ay, ax + STUB_LEN, ay, None
        else:
            return ax, ay, ax - STUB_LEN, ay, "R180"
    
    nets_xml = []
    seen_names = {}
    for name, pins in nets_raw:
        clean = sanitize_net_name(name)
        if clean in seen_names:
            seen_names[clean] += 1
            clean = f"{clean}_{seen_names[clean]}"
        else:
            seen_names[clean] = 0
        
        segments = []
        for (ref, pin) in pins:
            if (ref, pin) not in pin_abs:
                continue
            ax, ay, ex, ey, rot = stub_endpoint(ref, pin)
            rot_attr = f' rot="{rot}"' if rot else ''
            segments.append(
                f'<segment>'
                f'<pinref part="{ref}" gate="A" pin="P{pin}"/>'
                f'<wire x1="{ax}" y1="{ay}" x2="{ex}" y2="{ey}" width="0.1524" layer="91"/>'
                f'<label x="{ex}" y="{ey}" size="{LABEL_SIZE}" layer="95"{rot_attr} xref="yes"/>'
                f'</segment>'
            )
        if segments:
            nets_xml.append(f'<net name="{clean}" class="0">{"".join(segments)}</net>')
    
    sch = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="9.6.2">
<drawing>
<settings>
<setting alwaysvectorfont="no"/>
<setting verticaltext="up"/>
</settings>
<grid distance="0.1" unitdist="inch" unit="inch" style="lines" multiple="1" display="no" altdistance="0.01" altunitdist="inch" altunit="inch"/>
<layers>
{LAYERS}
</layers>
<schematic xreflabel="%F%N/%S.%C%R" xrefpart="/%S.%C%R">
<libraries>
<library name="EDA_IMPORT">
<packages></packages>
<symbols>
{''.join(symbols_xml)}
</symbols>
<devicesets>
{devicesets_xml}
</devicesets>
</library>
</libraries>
<attributes></attributes>
<variantdefs></variantdefs>
<classes>
<class number="0" name="default" width="0" drill="0"></class>
</classes>
<parts>
{''.join(parts_xml)}
</parts>
<sheets>
<sheet>
<plain></plain>
<instances>
{''.join(instances_xml)}
</instances>
<busses></busses>
<nets>
{''.join(nets_xml)}
</nets>
</sheet>
</sheets>
</schematic>
</drawing>
</eagle>
'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(sch)
    
    print(f"OK - {output_path}")
    print(f"Format: {data['format']}")
    print(f"Components: {len(refs)}")
    print(f"Nets: {len(nets_xml)}")

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def convert(input_path, output_path, clearance=8.0):
    data = prepare_data(input_path)
    extents, placement, _ = compute_layout(data, clearance=clearance)
    generate_sch(data, extents, placement, output_path)

def open_file_externally(path):
    try:
        os.startfile(path)
    except Exception:
        pass

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        clearance = float(sys.argv[3]) if len(sys.argv) >= 4 else 8.0
        convert(sys.argv[1], sys.argv[2], clearance=clearance)
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox, simpledialog
            import tempfile
            
            root = tk.Tk()
            root.withdraw()
            
            input_path = filedialog.askopenfilename(
                title="Select Netlist File",
                filetypes=[("Netlist files", "*.net *.cir *.asc *.txt"), ("All files", "*.*")]
            )
            if not input_path:
                sys.exit(0)
            
            print("Parsing netlist...")
            data = prepare_data(input_path)
            print(f"Format: {data['format']}")
            print(f"Components: {len(data['refs'])}, Nets: {len(data['nets_raw'])}")
            
            clearance = 8.0
            while True:
                clearance = simpledialog.askfloat(
                    "Clearance",
                    "Extra safety space between components (mm):",
                    initialvalue=clearance, minvalue=0.0
                )
                if clearance is None:
                    sys.exit(0)
                
                print(f"Computing layout with clearance={clearance}mm...")
                extents, placement, _ = compute_layout(data, clearance=clearance)
                
                try:
                    preview_path = tempfile.NamedTemporaryFile(suffix='.png', delete=False).name
                    render_preview(data['refs'], extents, placement, preview_path, clearance)
                    open_file_externally(preview_path)
                except ImportError:
                    messagebox.showwarning(
                        "Preview unavailable",
                        "matplotlib not installed: 'pip install matplotlib' to enable visual preview."
                    )
                
                answer = messagebox.askyesnocancel(
                    "Preview",
                    "Preview opened in image viewer.\n"
                    "YES = generate final .sch file with this clearance\n"
                    "NO = try again with different clearance\n"
                    "CANCEL = exit without generating"
                )
                
                if answer is None:
                    sys.exit(0)
                elif answer is True:
                    break
            
            output_path = filedialog.asksaveasfilename(
                title="Save Eagle Schematic as...",
                defaultextension=".sch",
                filetypes=[("Eagle Schematic", "*.sch")]
            )
            if not output_path:
                sys.exit(0)
            
            try:
                generate_sch(data, extents, placement, output_path)
                messagebox.showinfo("Completed", f"File generated:\n{output_path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
                raise
        
        except ImportError:
            print("tkinter not available.")
            print("Usage: python eda2eagle.py input.net output.sch [clearance_mm]")
            sys.exit(1)

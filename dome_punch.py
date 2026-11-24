# Copyright (c) 2026 Alejandro Cabrera Zuniga || Audio Brewers
# SPDX-License-Identifier: MIT  (see LICENSE.txt)

"""
Living Sky  -  TouchDesigner network builder
Audio Brewers

HOW TO USE
  1. Point a Text DAT at this file (File parameter, Sync to File on).
  2. Right-click the DAT > Run Script.
     Running it again UPDATES the existing 'dome_punch' COMP in place: wiring,
     exports and source parameter values are kept, new parameters are added,
     obsolete ones removed.
  3. Take the result from the COMP's output (fisheye domemaster by default).
  4. Route your OSC values into the source parameters.

The whole dome is a living, glowing fluid (aurora currents over a starfield).
  Transient = blast + swirl into the fluid at the source position.
  RMS       = continuous glow emitted by the source.
  Movement  = moving sources drag luminous streams.
  Centroid  = colour.   Focus = tight (coherent) vs wide (diffuse).

Coordinates: AmbiX unit vectors, +x front, +y left, +z up.
Needs TouchDesigner 2022 or later.
"""

NUM_SOURCES = 16
COMP_NAME = 'dome_punch'

host = me.parent()
NSRC = NUM_SOURCES + 1  # + dominant slot


# --------------------------------------------------------------------------
# Shared GLSL
# --------------------------------------------------------------------------
GLSL_COMMON = """
const float PI = 3.14159265359;
const float TAU = 6.28318530718;

vec3 dirFromAzEl(float az, float el) {
    return vec3(cos(el) * cos(az), cos(el) * sin(az), sin(el));
}

// output pixel -> direction. m.y = output map (0 fisheye, 1 equirect half),
// m.z = azimuth offset (deg), m.w = mirror
bool outDir(vec2 uv, vec4 m, out vec3 d) {
    float az, el;
    if (m.y < 0.5) {
        vec2 p = uv * 2.0 - 1.0;
        if (m.w > 0.5) p.x = -p.x;
        float r = length(p);
        if (r > 1.0) { d = vec3(0.0); return false; }
        el = (1.0 - r) * 0.5 * PI;
        az = atan(-p.x, p.y);
    } else {
        az = (0.5 - uv.x) * TAU;
        if (m.w > 0.5) az = -az;
        el = uv.y * 0.5 * PI;
    }
    az += radians(m.z);
    d = dirFromAzEl(az, el);
    return true;
}

// direction -> simulation (fisheye dome) uv; below horizon folds to the rim
vec2 dirToDome(vec3 d, vec4 m) {
    float el = asin(clamp(d.z, -1.0, 1.0));
    float az = atan(d.y, d.x) - radians(m.z);
    float r  = clamp(1.0 - max(el, 0.0) / (0.5 * PI), 0.0, 0.97);
    vec2 p = vec2(-sin(az), cos(az)) * r;
    if (m.w > 0.5) p.x = -p.x;
    return p * 0.5 + 0.5;
}

float hash31(vec3 p) {
    p = fract(p * 0.3183099 + 0.1);
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float vnoise3(vec3 x) {
    vec3 i = floor(x), f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash31(i + vec3(0, 0, 0)), hash31(i + vec3(1, 0, 0)), f.x),
                   mix(hash31(i + vec3(0, 1, 0)), hash31(i + vec3(1, 1, 0)), f.x), f.y),
               mix(mix(hash31(i + vec3(0, 0, 1)), hash31(i + vec3(1, 0, 1)), f.x),
                   mix(hash31(i + vec3(0, 1, 1)), hash31(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}

float fbm(vec3 p) {
    float v = 0.0, a = 0.5;
    for (int o = 0; o < 4; o++) { v += a * vnoise3(p); p *= 2.03; a *= 0.5; }
    return v / 0.9375;
}
"""

src_decl = '\n'.join('uniform vec4 uA%d;\nuniform vec4 uB%d;\nuniform vec4 uP%d;' % (i, i, i)
                     for i in range(NSRC))
src_fill = '\n'.join('    A[%d] = uA%d; B[%d] = uB%d; Pp[%d] = uP%d;' % (i, i, i, i, i, i)
                     for i in range(NSRC))

SRC_BLOCK = """
// per source: A = (x, y, z, envelope)  B = (focus, rms, centroid, hit count)
//             P = (lagged x, y, z, active)
@DECL@
#define NSRC @NSRC@
vec4 A[NSRC];
vec4 B[NSRC];
vec4 Pp[NSRC];

vec3 srcDir(vec3 v) {
    vec3 s = vec3(v.xy, max(v.z, 0.0));
    return length(s) < 1e-4 ? vec3(0.0, 0.0, 1.0) : normalize(s);
}
""".replace('@DECL@', src_decl).replace('@NSRC@', str(NSRC))


def finish(code):
    return (code.replace('@COMMON@', GLSL_COMMON).replace('@SRC@', SRC_BLOCK)
            .replace('@FILL@', src_fill))


# --------------------------------------------------------------------------
# Velocity simulation  (input 0 = previous velocity)
# --------------------------------------------------------------------------
VEL_SHADER = finish("""// Living Sky - velocity. Generated, do not edit (rerun the builder)
uniform vec4 uSim;   // dt, time, velocity fade (s), vorticity
uniform vec4 uAmb;   // flow strength, flow scale, flow speed, rotation
uniform vec4 uHit;   // push, swirl, radius focused (deg), radius diffuse (deg)
uniform vec4 uDrag;  // drag, rms > size, -, -
uniform vec4 uMap;   // -, -, az offset, mirror
out vec4 fragColor;
@COMMON@
@SRC@

vec2 V(vec2 uv) { return texture(sTD2DInputs[0], uv).xy; }

float curlAt(vec2 uv, vec2 px) {
    return (V(uv + vec2(px.x, 0.0)).y - V(uv - vec2(px.x, 0.0)).y) / (2.0 * px.x)
         - (V(uv + vec2(0.0, px.y)).x - V(uv - vec2(0.0, px.y)).x) / (2.0 * px.y);
}

vec2 flowField(vec2 p, float t) {
    float e = 0.02;
    vec3 q = vec3(p, t);
    float n1 = fbm(q + vec3(0.0, e, 0.0)), n2 = fbm(q - vec3(0.0, e, 0.0));
    float n3 = fbm(q + vec3(e, 0.0, 0.0)), n4 = fbm(q - vec3(e, 0.0, 0.0));
    return vec2(n1 - n2, -(n3 - n4)) / (2.0 * e);
}

void main() {
    vec2 uv = vUV.st;
    vec2 c  = uv * 2.0 - 1.0;
    float r = length(c);
    if (r > 1.0) { fragColor = TDOutputSwizzle(vec4(0.0)); return; }

@FILL@

    float dt = uSim.x;
    vec2 px  = 1.0 / vec2(textureSize(sTD2DInputs[0], 0));

    // advect + fade
    vec2 v  = V(uv);
    vec2 vn = V(uv - v * dt) * exp(-dt / max(uSim.z, 0.01));

    // vorticity confinement (keeps the swirls alive)
    float cC = curlAt(uv, px);
    vec2 grad = vec2(abs(curlAt(uv + vec2(px.x, 0.0), px)) - abs(curlAt(uv - vec2(px.x, 0.0), px)),
                     abs(curlAt(uv + vec2(0.0, px.y), px)) - abs(curlAt(uv - vec2(0.0, px.y), px)));
    grad /= length(grad) + 1e-5;
    vn += uSim.w * px.x * vec2(grad.y, -grad.x) * cC * dt * 60.0;

    // ambient flow: curl noise + slow rotation around the zenith
    vn += (flowField(uv * uAmb.y, uSim.y * uAmb.z) * uAmb.x * 0.05
           + vec2(-c.y, c.x) * uAmb.w * 0.1) * dt;

    // sources
    for (int i = 0; i < NSRC; i++) {
        if (Pp[i].w < 0.5) continue;
        float t     = clamp(A[i].w, 0.0, 1.0);
        float focus = clamp(B[i].x, 0.0, 1.0);
        float rms   = clamp(B[i].y, 0.0, 1.0);
        vec2  p     = dirToDome(srcDir(A[i].xyz), uMap);
        float R     = mix(uHit.w, uHit.z, focus) / 180.0 * (1.0 + (rms - 0.5) * uDrag.y);
        vec2  dv    = uv - p;
        float dist  = length(dv);
        float g     = exp(-dist * dist / (R * R));
        if (g < 1e-4) continue;
        vec2  dir   = dist > 1e-5 ? dv / dist : vec2(0.0);
        vec2  perp  = vec2(-dir.y, dir.x);
        float spin  = mod(B[i].w, 2.0) < 0.5 ? 1.0 : -1.0;

        float ang   = dist > 1e-5 ? atan(dir.y, dir.x) : 0.0;
        float fing  = vnoise3(vec3(cos(ang) * 3.0, sin(ang) * 3.0, B[i].w * 1.7 + float(i)));
        vn += (dir * uHit.x * (0.2 + 1.6 * fing) + perp * uHit.y * spin) * t * g * dt * 10.0;   // blast (fingered) + swirl

        vec2 pl = dirToDome(srcDir(Pp[i].xyz), uMap);                       // movement drag
        vn += (p - pl) / 0.1 * uDrag.x * (0.3 + rms) * g * dt * 4.0;
    }

    vn *= smoothstep(1.0, 0.94, r);   // calm rim
    float sp = length(vn);
    if (sp > 2.0) vn *= 2.0 / sp;      // stability
    if (any(isnan(vn))) vn = vec2(0.0);
    fragColor = TDOutputSwizzle(vec4(vn, cC, 1.0));
}
""")


# --------------------------------------------------------------------------
# Dye (light) simulation  (input 0 = previous dye, input 1 = velocity)
# --------------------------------------------------------------------------
DYE_SHADER = finish("""// Living Sky - light. Generated, do not edit (rerun the builder)
uniform vec4 uSim;   // dt, time, light fade (s), -
uniform vec4 uHitD;  // hit brightness, glow brightness, radius focused (deg), radius diffuse (deg)
uniform vec4 uAur;   // aurora amount, scale, speed, thinness
uniform vec4 uAur2;  // colour drift, level (mean rms), level > aurora, rms > size
uniform vec4 uPal0;  // hit colour low rgb, centroid > colour
uniform vec4 uPal1;  // hit colour mid rgb
uniform vec4 uPal2;  // hit colour high rgb
uniform vec4 uAurA;  // aurora colour A rgb
uniform vec4 uAurB;  // aurora colour B rgb
uniform vec4 uMap;
out vec4 fragColor;
@COMMON@
@SRC@

vec3 palette(float c) {
    vec3 p = c < 0.5 ? mix(uPal0.rgb, uPal1.rgb, c * 2.0)
                     : mix(uPal1.rgb, uPal2.rgb, (c - 0.5) * 2.0);
    return mix(uPal1.rgb, p, uPal0.w);
}

void main() {
    vec2 uv = vUV.st;
    float r = length(uv * 2.0 - 1.0);
    if (r > 1.0) { fragColor = TDOutputSwizzle(vec4(0.0)); return; }

@FILL@

    float dt = uSim.x, time = uSim.y;
    vec2 v   = texture(sTD2DInputs[1], uv).xy;
    vec2 px  = 1.0 / vec2(textureSize(sTD2DInputs[1], 0));
    float div = (texture(sTD2DInputs[1], uv + vec2(px.x, 0.0)).x - texture(sTD2DInputs[1], uv - vec2(px.x, 0.0)).x) / (2.0 * px.x)
              + (texture(sTD2DInputs[1], uv + vec2(0.0, px.y)).y - texture(sTD2DInputs[1], uv - vec2(0.0, px.y)).y) / (2.0 * px.y);
    vec3 dye = texture(sTD2DInputs[0], uv - v * dt).rgb * exp(-dt / max(uSim.z, 0.01));
    dye *= clamp(1.0 - div * dt, 0.0, 1.25);   // expanding light thins, compressed light brightens
    dye *= mix(1.0, exp(-dt * 4.0), smoothstep(0.92, 1.0, r));   // no build-up at the rim

    // aurora: thin ridged ribbons, always alive
    float tt   = time * uAur.z;
    float n    = fbm(vec3(uv * uAur.y, tt));
    float rib  = pow(1.0 - abs(2.0 * n - 1.0), uAur.w);
    float sel  = fbm(vec3(uv * uAur.y * 0.4 + 7.3, tt * 0.6 + time * uAur2.x));
    vec3  ac   = mix(uAurA.rgb, uAurB.rgb, smoothstep(0.35, 0.65, sel));
    float mask = smoothstep(0.4, 0.75, fbm(vec3(uv * uAur.y * 0.35 - 3.1, tt * 0.5)));
    float lvl  = uAur.x * (1.0 + uAur2.z * uAur2.y);
    dye += ac * rib * mask * lvl * dt;

    // sources
    for (int i = 0; i < NSRC; i++) {
        if (Pp[i].w < 0.5) continue;
        float t     = clamp(A[i].w, 0.0, 1.0);
        float focus = clamp(B[i].x, 0.0, 1.0);
        float rms   = clamp(B[i].y, 0.0, 1.0);
        float cen   = clamp(B[i].z, 0.0, 1.0);
        vec2  p     = dirToDome(srcDir(A[i].xyz), uMap);
        float R     = mix(uHitD.w, uHitD.z, focus) / 180.0 * (1.0 + (rms - 0.5) * uAur2.w);
        vec2  dv    = uv - p;
        float g     = exp(-dot(dv, dv) / (R * R));
        if (g < 1e-4) continue;
        vec3 col = mix(palette(cen), vec3(1.0), 0.3 * t * g * g);
        float g2 = exp(-dot(dv, dv) / (R * R * 0.25));
        dye += col * (t * uHitD.x * g * 10.0 + rms * uHitD.y * g2) * dt;
    }

    fragColor = TDOutputSwizzle(vec4(min(dye, vec3(50.0)), 1.0));
}
""")


# --------------------------------------------------------------------------
# Look: sky, stars, bloom, tone mapping
# inputs: 0 = light, 1 = bloom small, 2 = bloom wide
# --------------------------------------------------------------------------
LOOK_SHADER = finish("""// Living Sky - look. Generated, do not edit (rerun the builder)
uniform vec4 uMap;    // -, output map, az offset, mirror
uniform vec4 uLook1;  // exposure, bloom, bloom wide, chroma
uniform vec4 uLook2;  // striation, stars, grain, -
uniform vec4 uSky0;   // zenith rgb, time
uniform vec4 uSky1;   // horizon rgb, star density
out vec4 fragColor;
@COMMON@

vec3 aces(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

void main() {
    vec3 d;
    if (!outDir(vUV.st, uMap, d)) { fragColor = TDOutputSwizzle(vec4(0.0, 0.0, 0.0, 1.0)); return; }
    float time = uSky0.w;
    vec2 duv = uMap.y < 0.5 ? vUV.st : dirToDome(d, uMap);
    float el = asin(clamp(d.z, -1.0, 1.0));
    float az = atan(d.y, d.x);

    // light, with slight chromatic spread toward the horizon
    vec2 cc = duv - 0.5;
    float ch = uLook1.w * 0.02;
    vec3 lit = vec3(texture(sTD2DInputs[0], 0.5 + cc * (1.0 + ch)).r,
                    texture(sTD2DInputs[0], duv).g,
                    texture(sTD2DInputs[0], 0.5 + cc * (1.0 - ch)).b);

    // aurora-like vertical striations
    float st = vnoise3(vec3(az * 60.0, el * 3.0, time * 0.4));
    lit *= mix(1.0, 0.45 + 1.1 * st, uLook2.x * smoothstep(1.3, 0.7, el));

    vec3 bloom = texture(sTD2DInputs[1], duv).rgb * uLook1.y
               + texture(sTD2DInputs[2], duv).rgb * uLook1.z;

    // night sky + twinkling stars
    vec3 sky = mix(uSky1.rgb, uSky0.rgb, pow(max(d.z, 0.0), 0.6));
    vec3 q = d * uSky1.w;
    vec3 cell = floor(q);
    vec3 f = fract(q) - 0.5;
    float h = hash31(cell);
    float star = step(0.97, h) * exp(-dot(f, f) * 90.0)
               * (0.55 + 0.45 * sin(time * (1.0 + h * 6.0) + h * 100.0)) * (h - 0.97) * 60.0;
    float lum = dot(lit + bloom, vec3(0.3, 0.6, 0.1));
    vec3 starCol = mix(vec3(0.75, 0.85, 1.0), vec3(1.0, 0.9, 0.75), fract(h * 37.0));
    sky += starCol * star * uLook2.y * exp(-lum * 3.0);

    vec3 col = sky + (lit + bloom) * uLook1.x;
    col = aces(col);
    col += (hash31(vec3(vUV.st * 1731.0, time * 60.0)) - 0.5) * uLook2.z;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
""")



# --------------------------------------------------------------------------
# Presets (Parameter Execute DAT callbacks)
# --------------------------------------------------------------------------
PRESET_CODE = """# Living Sky presets. Generated by the builder, do not edit.
# Presets are JSON files in a 'presets' folder next to the .toe.
import os, json, re

SKIP_PAGES = ('Projection', 'Dominant', 'Presets')
SKIP_PARS = ('Srccount',)

def folder():
    f = os.path.join(project.folder, 'presets')
    os.makedirs(f, exist_ok=True)
    return f

def preset_pars(comp):
    for p in comp.customPars:
        pg = p.page.name
        if pg in SKIP_PAGES or pg.startswith('Src') or p.name in SKIP_PARS:
            continue
        if p.isPulse:
            continue
        yield p

def refresh(comp):
    names = sorted(os.path.splitext(f)[0] for f in os.listdir(folder())
                   if f.lower().endswith('.json'))
    par = comp.par.Preset
    cur = par.eval()
    par.menuNames = names or ['none']
    par.menuLabels = names or ['(no presets)']
    if cur in names:
        par.val = cur
    return names

def save(comp, name=None):
    name = (name or comp.par.Presetname.eval() or 'preset').strip()
    name = re.sub(r'[^A-Za-z0-9 _-]', '_', name) or 'preset'
    data = {'type': 'LivingSky', 'version': 1, 'name': name,
            'params': {p.name: p.eval() for p in preset_pars(comp)}}
    with open(os.path.join(folder(), name + '.json'), 'w') as f:
        json.dump(data, f, indent=2)
    refresh(comp)
    comp.par.Preset.val = name
    print('Living Sky: saved preset', name)

def load(comp, name=None):
    name = name or comp.par.Preset.eval()
    path = os.path.join(folder(), name + '.json')
    if not os.path.isfile(path):
        print('Living Sky: no preset', path)
        return
    with open(path) as f:
        params = json.load(f).get('params', {})
    dur = max(comp.par.Presettime.eval(), 0.0)
    targets = {}
    for k, v in params.items():
        p = comp.par[k]
        if p is None or p.mode != ParMode.CONSTANT:
            continue    # missing, or driven by an export / expression
        numeric = (isinstance(v, (int, float)) and not isinstance(v, bool)
                   and not (p.isToggle or p.isMenu or p.isString))
        if dur > 0 and numeric:
            targets[k] = (float(p.eval()), float(v), bool(p.isInt))
        else:
            try:
                p.val = v
            except Exception:
                pass
    comp.par.Presetname.val = name
    mid = comp.fetch('morph_id', 0) + 1
    comp.store('morph_id', mid)
    comp.store('morph', {'start': absTime.seconds, 'dur': dur, 'targets': targets})
    step(comp.path, mid)
    print('Living Sky: loading preset', name, 'over %.2f s' % dur)

def step(path, mid):
    comp = op(path)
    if comp is None or comp.fetch('morph_id', 0) != mid:
        return    # superseded by a newer load
    m = comp.fetch('morph', None)
    if not m:
        return
    t = 1.0 if m['dur'] <= 0 else min((absTime.seconds - m['start']) / m['dur'], 1.0)
    e = t * t * (3.0 - 2.0 * t)
    for k, (a, b, isint) in m['targets'].items():
        p = comp.par[k]
        if p is None or p.mode != ParMode.CONSTANT:
            continue
        v = a + (b - a) * e
        p.val = int(round(v)) if isint else v
    if t < 1.0:
        run("op(%r).module.step(%r, %d)" % (me.path, path, mid), delayFrames=1)

def onPulse(par):
    comp = par.owner
    if par.name == 'Presetsave':
        save(comp)
    elif par.name == 'Presetload':
        load(comp)
    elif par.name == 'Presetrefresh':
        refresh(comp)
    return

def onValueChange(par, prev):
    return
"""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
KEEP = set()


def has(name):
    return comp.par[name] is not None


def page(name):
    for pg in comp.customPages:
        if pg.name == name:
            return pg
    return comp.appendCustomPage(name)


def node(optype, name, x, y):
    o = comp.op(name)
    if o is None:
        o = comp.create(optype, name)
    o.nodeX, o.nodeY = x, y
    return o


def fpar(pg, name, label, default, lo, hi, clamp=False):
    KEEP.add(name)
    if has(name):
        return comp.par[name]
    p = pg.appendFloat(name, label=label)[0]
    p.default = p.val = default
    p.normMin, p.normMax = lo, hi
    if clamp:
        p.clampMin = p.clampMax = True
        p.min, p.max = lo, hi
    return p


def ipar(pg, name, label, default, lo, hi):
    KEEP.add(name)
    if has(name):
        return comp.par[name]
    p = pg.appendInt(name, label=label)[0]
    p.default = p.val = default
    p.normMin, p.normMax = lo, hi
    p.clampMin = p.clampMax = True
    p.min, p.max = lo, hi
    return p


def tpar(pg, name, label, default):
    KEEP.add(name)
    if has(name):
        return comp.par[name]
    p = pg.appendToggle(name, label=label)[0]
    p.default = p.val = default
    return p


def rgbpar(pg, name, label, default):
    KEEP.update(name + c for c in 'rgb')
    if has(name + 'r'):
        return
    for p, v in zip(pg.appendRGB(name, label=label), default):
        p.default = p.val = v


def xyzpar(pg, name, label, default):
    KEEP.update(name + c for c in 'xyz')
    if has(name + 'x'):
        return
    for p, v in zip(pg.appendXYZ(name, label=label), default):
        p.default = p.val = v
        p.normMin, p.normMax = -1, 1


def menupar(pg, name, label, names, labels):
    KEEP.add(name)
    if has(name):
        return comp.par[name]
    p = pg.appendMenu(name, label=label)[0]
    p.menuNames, p.menuLabels = names, labels
    p.default = p.val = names[0]
    return p


def source_params(pg, prefix, label):
    xyzpar(pg, prefix + 'pos', label + ' Position', (1, 0, 0))
    fpar(pg, prefix + 'transient', label + ' Transient', 0, 0, 1, True)
    fpar(pg, prefix + 'focus', label + ' Focus', 1, 0, 1, True)
    fpar(pg, prefix + 'rms', label + ' RMS', 0, 0, 1, True)
    fpar(pg, prefix + 'centroid', label + ' Centroid', 0.5, 0, 1, True)


def set_expr(par, expr):
    par.expr = expr
    par.mode = ParMode.EXPRESSION


def try_set(o, parname, value=None, expr=None):
    p = getattr(o.par, parname, None)
    if p is None:
        print('Living Sky: %s has no parameter %s' % (o.path, parname))
        return
    try:
        if expr is not None:
            set_expr(p, expr)
        else:
            p.val = value
    except Exception as e:
        print('Living Sky: could not set %s.%s (%s)' % (o.path, parname, e))


def set_vec_uniforms(glsl, uniforms):
    glsl.seq.vec.numBlocks = len(uniforms)
    for i, (name, vals) in enumerate(uniforms):
        glsl.par['vec%dname' % i].val = name
        for c, v in zip('xyzw', vals):
            p = glsl.par['vec%dvalue%s' % (i, c)]
            if isinstance(v, str):
                set_expr(p, v)
            else:
                p.mode = ParMode.CONSTANT
                p.val = v


def res(top, w, h, fmt):
    top.par.outputresolution = 'custom'
    set_expr(top.par.resolutionw, w)
    set_expr(top.par.resolutionh, h)
    try_set(top, 'format', fmt)


def wire(dst, srcs):
    """Connect srcs to dst inputs 0..n in order (safe on re-runs)."""
    for c in dst.inputConnectors:
        c.disconnect()
    for i, s in enumerate(srcs):
        dst.inputConnectors[i].connect(s)


def shader(name, code, x, y):
    dat = node(textDAT, name + '_pixel', x, y - 150)
    dat.text = code
    g = node(glslTOP, name, x, y)
    g.par.pixeldat = dat.name
    return g


# --------------------------------------------------------------------------
# Comp + removal of previous versions' internals
# --------------------------------------------------------------------------
comp = host.op(COMP_NAME)
updating = comp is not None
if not updating:
    comp = host.create(baseCOMP, COMP_NAME)
    comp.nodeX, comp.nodeY = me.nodeX + 250, me.nodeY

for name in ('glsl_punch', 'punch_pixel', 'glsl_fx', 'fx_pixel', 'trails', 'trail_pixel',
             'feedback', 'composite', 'comp_pixel', 'transients_slow'):
    o = comp.op(name)
    if o:
        o.destroy()


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
pg = page('Projection')
menupar(pg, 'Outputmap', 'Output Mapping',
        ['fisheye', 'equirecthalf'],
        ['Fisheye domemaster', 'Equirect (upper half)'])
ipar(pg, 'Resolution', 'Output Res (1=1080 2=2048 3=3K 4=4096)', 2, 1, 4)
ipar(pg, 'Simres', 'Simulation Resolution', 1024, 256, 2048)
fpar(pg, 'Azoffset', 'Azimuth Offset (deg)', 0, -180, 180)
tpar(pg, 'Mirror', 'Mirror', False)

pg = page('Global')
ipar(pg, 'Srccount', 'Source Count', 4, 1, NUM_SOURCES)
fpar(pg, 'Release', 'Hit Release (s)', 0.12, 0.01, 2)
fpar(pg, 'Hitthreshold', 'Hit Threshold', 0.3, 0.01, 1, True)

pg = page('Fluid')
fpar(pg, 'Flow', 'Ambient Flow', 1, 0, 4)
fpar(pg, 'Flowscale', 'Flow Scale', 2.5, 0.2, 10)
fpar(pg, 'Flowspeed', 'Flow Evolve', 0.08, 0, 1)
fpar(pg, 'Rotation', 'Sky Rotation', 0.15, -2, 2)
fpar(pg, 'Vorticity', 'Swirliness', 0.3, 0, 2)
fpar(pg, 'Velfade', 'Motion Fade (s)', 2.5, 0.1, 20)
fpar(pg, 'Dyefade', 'Light Fade (s)', 3.5, 0.1, 30)

pg = page('Audio')
fpar(pg, 'Push', 'Hit Blast', 0.6, 0, 5)
fpar(pg, 'Swirl', 'Hit Swirl', 1.5, 0, 5)
fpar(pg, 'Hitbright', 'Hit Brightness', 2, 0, 20)
fpar(pg, 'Glowbright', 'RMS Glow', 0.25, 0, 5)
fpar(pg, 'Drag', 'Movement Drag', 1, 0, 5)
fpar(pg, 'Radiusfocus', 'Radius Focused (deg)', 10, 1, 90)
fpar(pg, 'Radiusdiffuse', 'Radius Diffuse (deg)', 35, 1, 90)
fpar(pg, 'Rmstosize', 'RMS > Size', 0.5, 0, 1, True)
fpar(pg, 'Centroidtocolor', 'Centroid > Colour', 1, 0, 1, True)
rgbpar(pg, 'Colorlow', 'Hit Colour Low (dark sound)', (1.0, 0.35, 0.08))
rgbpar(pg, 'Colormid', 'Hit Colour Mid', (1.0, 0.2, 0.55))
rgbpar(pg, 'Colorhigh', 'Hit Colour High (bright sound)', (0.35, 0.85, 1.0))

pg = page('Aurora')
fpar(pg, 'Aurora', 'Aurora Amount', 0.25, 0, 4)
fpar(pg, 'Aurorascale', 'Ribbon Scale', 3, 0.5, 12)
fpar(pg, 'Auroraspeed', 'Ribbon Evolve', 0.05, 0, 1)
fpar(pg, 'Aurorathin', 'Ribbon Thinness', 10, 1, 32)
fpar(pg, 'Auroradrift', 'Colour Drift', 0.03, 0, 1)
fpar(pg, 'Levelreact', 'Level > Aurora', 1.5, 0, 6)
rgbpar(pg, 'Auroraa', 'Aurora Colour A', (0.1, 1.0, 0.55))
rgbpar(pg, 'Aurorab', 'Aurora Colour B', (0.45, 0.2, 1.0))

pg = page('Look')
fpar(pg, 'Exposure', 'Exposure', 1, 0, 4)
fpar(pg, 'Bloom', 'Bloom', 0.6, 0, 3)
fpar(pg, 'Bloomwide', 'Bloom Wide', 0.4, 0, 3)
fpar(pg, 'Chroma', 'Chromatic Spread', 0.5, 0, 3)
fpar(pg, 'Striation', 'Striation', 0.3, 0, 1, True)
fpar(pg, 'Stars', 'Stars', 1, 0, 3)
fpar(pg, 'Stardensity', 'Star Density', 220, 50, 600)
fpar(pg, 'Grain', 'Grain', 0.015, 0, 0.1)
rgbpar(pg, 'Skytop', 'Sky Zenith', (0.0, 0.004, 0.02))
rgbpar(pg, 'Skyhorizon', 'Sky Horizon', (0.015, 0.025, 0.07))

pg = page('Presets')
KEEP.update(('Presetname', 'Presetsave', 'Preset', 'Presetload', 'Presetrefresh'))
if not has('Presetname'):
    pg.appendStr('Presetname', label='Preset Name')
if not has('Presetsave'):
    pg.appendPulse('Presetsave', label='Save Preset')
menupar(pg, 'Preset', 'Preset', ['none'], ['(no presets)'])
fpar(pg, 'Presettime', 'Transition Time (s)', 2, 0, 10, True)
if not has('Presetload'):
    pg.appendPulse('Presetload', label='Load Preset')
if not has('Presetrefresh'):
    pg.appendPulse('Presetrefresh', label='Refresh List')

pg = page('Dominant')
tpar(pg, 'Domon', 'Use Dominant Source', False)
source_params(pg, 'Dom', 'Dom')

for start in range(0, NUM_SOURCES, 4):
    pg = page('Src %d-%d' % (start, min(start + 3, NUM_SOURCES - 1)))
    for i in range(start, min(start + 4, NUM_SOURCES)):
        source_params(pg, 'S%d' % i, 'S%d' % i)

# remove parameters from previous versions
for p in list(comp.customPars):
    try:
        if p.valid and p.name not in KEEP:
            p.destroy()
    except Exception:
        pass
for pg in list(comp.customPages):
    try:
        if not pg.pars:
            pg.destroy()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
P = lambda n: 'parent().par.%s.eval()' % n
SIM = P('Simres')
OUTW = OUTH = '[1080, 2048, 3072, 4096][int(parent().par.Resolution.eval()) - 1]'
DT = '1.0 / max(project.cookRate, 1)'
TIME = 'absTime.seconds'


def pre(i):
    return 'Dom' if i == NUM_SOURCES else 'S%d' % i


def active(i):
    return (P('Domon') if i == NUM_SOURCES
            else '(1 if %d < parent().par.Srccount.eval() else 0)' % i)


inp = node(inTOP, 'in1', 0, 300)
old_core = comp.op('core')
if old_core:
    old_core.destroy()

# CHOPs
tin = node(constantCHOP, 'transients_in', 0, -500)
tin.seq.const.numBlocks = NSRC
for i in range(NSRC):
    tin.par['const%dname' % i].val = 't%d' % i
    set_expr(tin.par['const%dvalue' % i], '%s * %s' % (P(pre(i) + 'transient'), active(i)))

env = node(lagCHOP, 'transients_lag', 200, -500)
env.inputConnectors[0].connect(tin)
env.par.lag1 = 0
set_expr(env.par.lag2, P('Release'))

hits = node(countCHOP, 'hits', 200, -650)
hits.inputConnectors[0].connect(tin)
try_set(hits, 'threshold', True)
try_set(hits, 'threshup', expr=P('Hitthreshold'))
try_set(hits, 'threshdown', expr=P('Hitthreshold') + ' * 0.5')

pin = node(constantCHOP, 'pos_in', 0, -800)
pin.seq.const.numBlocks = NSRC * 3
for i in range(NSRC):
    for k, ax in enumerate('xyz'):
        j = i * 3 + k
        pin.par['const%dname' % j].val = 'p%s%d' % (ax, i)
        set_expr(pin.par['const%dvalue' % j], P(pre(i) + 'pos' + ax))

plag = node(lagCHOP, 'pos_lag', 200, -800)
plag.inputConnectors[0].connect(pin)
plag.par.lag1 = 0.1
plag.par.lag2 = 0.1

# source uniforms (shared by both sims)
src_uniforms = []
for i in range(NSRC):
    src_uniforms += [
        ('uA%d' % i, [P(pre(i) + 'posx'), P(pre(i) + 'posy'), P(pre(i) + 'posz'),
                      "op('transients_lag')['t%d'].eval()" % i]),
        ('uB%d' % i, [P(pre(i) + 'focus'), P(pre(i) + 'rms'), P(pre(i) + 'centroid'),
                      "op('hits')['t%d'].eval()" % i]),
        ('uP%d' % i, ["op('pos_lag')['px%d'].eval()" % i, "op('pos_lag')['py%d'].eval()" % i,
                      "op('pos_lag')['pz%d'].eval()" % i, active(i)]),
    ]
MAP = [0, 0, P('Azoffset'), P('Mirror')]
mean_rms = '(%s) / max(1, parent().par.Srccount.eval())' % ' + '.join(
    '%s * %s' % (P(pre(i) + 'rms'), active(i)) for i in range(NUM_SOURCES))

# seed for the feedback loops
black = node(constantTOP, 'black', 0, 0)
res(black, SIM, SIM, 'rgba32float')
try_set(black, 'alpha', 0)

# velocity loop
vfb = node(feedbackTOP, 'vel_fb', 200, 0)
vfb.inputConnectors[0].connect(black)
vel = shader('vel_sim', VEL_SHADER, 400, 0)
res(vel, SIM, SIM, 'rgba32float')
vel.inputConnectors[0].connect(vfb)
vfb.par.top = vel.name
set_vec_uniforms(vel, src_uniforms + [
    ('uSim',  [DT, TIME, P('Velfade'), P('Vorticity')]),
    ('uAmb',  [P('Flow'), P('Flowscale'), P('Flowspeed'), P('Rotation')]),
    ('uHit',  [P('Push'), P('Swirl'), P('Radiusfocus'), P('Radiusdiffuse')]),
    ('uDrag', [P('Drag'), P('Rmstosize'), 0, 0]),
    ('uMap',  MAP),
])

# light (dye) loop
dfb = node(feedbackTOP, 'dye_fb', 200, -250)
dfb.inputConnectors[0].connect(black)
dye = shader('dye_sim', DYE_SHADER, 600, -250)
res(dye, SIM, SIM, 'rgba32float')
wire(dye, [dfb, vel])
dfb.par.top = dye.name
set_vec_uniforms(dye, src_uniforms + [
    ('uSim',  [DT, TIME, P('Dyefade'), 0]),
    ('uHitD', [P('Hitbright'), P('Glowbright'), P('Radiusfocus'), P('Radiusdiffuse')]),
    ('uAur',  [P('Aurora'), P('Aurorascale'), P('Auroraspeed'), P('Aurorathin')]),
    ('uAur2', [P('Auroradrift'), mean_rms, P('Levelreact'), P('Rmstosize')]),
    ('uPal0', [P('Colorlowr'), P('Colorlowg'), P('Colorlowb'), P('Centroidtocolor')]),
    ('uPal1', [P('Colormidr'), P('Colormidg'), P('Colormidb'), 0]),
    ('uPal2', [P('Colorhighr'), P('Colorhighg'), P('Colorhighb'), 0]),
    ('uAurA', [P('Auroraar'), P('Auroraag'), P('Auroraab'), 0]),
    ('uAurB', [P('Aurorabr'), P('Aurorabg'), P('Aurorabb'), 0]),
    ('uMap',  MAP),
])

# bloom
b1 = node(blurTOP, 'bloom_small', 800, -150)
b1.inputConnectors[0].connect(dye)
set_expr(b1.par.size, '%s / 128.0' % SIM)
b2 = node(blurTOP, 'bloom_wide', 800, -350)
b2.inputConnectors[0].connect(dye)
try_set(b2, 'preshrink', 2)
set_expr(b2.par.size, '%s / 64.0' % SIM)

# look
look = shader('look', LOOK_SHADER, 1000, -250)
res(look, OUTW, OUTH, 'rgba16float')
wire(look, [dye, b1, b2])
set_vec_uniforms(look, [
    ('uMap',   [0, 'parent().par.Outputmap.menuIndex',
                P('Azoffset'), P('Mirror')]),
    ('uLook1', [P('Exposure'), P('Bloom'), P('Bloomwide'), P('Chroma')]),
    ('uLook2', [P('Striation'), P('Stars'), P('Grain'), 0]),
    ('uSky0',  [P('Skytopr'), P('Skytopg'), P('Skytopb'), TIME]),
    ('uSky1',  [P('Skyhorizonr'), P('Skyhorizong'), P('Skyhorizonb'), P('Stardensity')]),
])

out = node(outTOP, 'out1', 1200, -250)
out.inputConnectors[0].connect(look)

# presets
pe = node(parameterexecuteDAT, 'preset_callbacks', 0, -1000)
pe.text = PRESET_CODE
try_set(pe, 'op', '..')
try_set(pe, 'pars', 'Presetsave Presetload Presetrefresh')
try_set(pe, 'onpulse', True)
try_set(pe, 'valuechange', False)
try:
    if 'default' not in pe.module.refresh(comp):
        pe.module.save(comp, 'default')
    pe.module.refresh(comp)
except Exception as e:
    print('Living Sky: preset setup skipped (%s)' % e)

comp.viewer = True
print('Living Sky %s:' % ('updated' if updating else 'built'), comp.path)

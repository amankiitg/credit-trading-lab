import React, { useState, useEffect, useMemo } from "react";

// ------------------------------------------------------------------
// Private-credit CLO simulation (illustrative)
// Ratings evolve as PROBABILITY DISTRIBUTIONS via the transition matrix:
//   p(q) = p(q-1) · T        (start: one-hot at rating.d0)
// weighted mult = Σ p·mult ;  weighted PD̄ = Σ p·PD (copula check)
// spread = BSL industry curve + basis × AI × weighted mult
// mark   = 100 − DUR × (spread − spread₀)/100 ;  default → 1 − LGD
// ------------------------------------------------------------------

const INK = "#1B2A41", SUBTLE = "#6B7686", PAPER = "#EDF0F4", PANEL = "#FFFFFF",
  LINE = "#C9D2DE", CRIMSON = "#C8102E", NAVY = "#2C4A73", TEAL = "#1F8A70",
  GRAYED = "#9AA4B2";

const RATINGS = ["5", "5-", "6", "6-", "7"];
const RC = { "5": "#2E7D5B", "5-": "#6FA24B", "6": "#C9A227", "6-": "#D07A2E", "7": "#B0503C" };
const MULT = [1.2, 1.4, 1.6, 1.8, 2.0];
const PD = [0.004, 0.007, 0.011, 0.019, 0.032];
const LGD = 0.55, DUR = 3.0, RECOVERY = 100 * (1 - LGD);

// quarterly transition matrix (rows = from-rating: 5, 5-, 6, 6-, 7)
const T = [
  [0.85, 0.12, 0.03, 0.0, 0.0],
  [0.05, 0.8, 0.12, 0.03, 0.0],
  [0.01, 0.06, 0.78, 0.12, 0.03],
  [0.0, 0.02, 0.1, 0.73, 0.15],
  [0.0, 0.0, 0.04, 0.12, 0.84],
];

const LOANS = [
  { name: "Alpha Energy", ind: "Energy", r0: "6", ai: 1.0, basis: 250, bsl0: 395,
    bsl: [400, 415, 430, 420], u: [0.62, 0.48, 0.71, 0.55], defQ: null },
  { name: "MedCore Health", ind: "Healthcare", r0: "5", ai: 1.0, basis: 200, bsl0: 318,
    bsl: [320, 325, 335, 330], u: [0.83, 0.57, 0.44, 0.69], defQ: null },
  { name: "ByteWorks Software", ind: "Technology", r0: "6-", ai: 1.15, basis: 225, bsl0: 375,
    bsl: [380, 390, 405, 400], u: [0.35, 0.52, 0.27, 0.61], defQ: null },
  { name: "Forge Industrial", ind: "Industrials", r0: "7", ai: 1.0, basis: 275, bsl0: 445,
    bsl: [450, 470, 470, 470], u: [0.18, 0.011, null, null], defQ: 1 }, // defaults in Q2
  { name: "RetailCo Consumer", ind: "Consumer", r0: "6", ai: 1.05, basis: 215, bsl0: 355,
    bsl: [360, 370, 385, 375], u: [0.74, 0.66, 0.58, 0.81], defQ: null },
];

const matVec = (p) => T[0].map((_, j) => p.reduce((s, pi, i) => s + pi * T[i][j], 0));
const dot = (p, v) => p.reduce((s, pi, i) => s + pi * v[i], 0);

const BOOK = LOANS.map((L) => {
  let p = RATINGS.map((r) => (r === L.r0 ? 1 : 0));
  const dist = [p], wpd = [], mult = [], spread = [], mark = [];
  const s0 = L.bsl0 + L.basis * L.ai * dot(p, MULT);
  for (let q = 0; q < 4; q++) {
    if (L.defQ !== null && q > L.defQ) {
      dist.push(null); wpd.push(null); mult.push(null); spread.push(null); mark.push(RECOVERY);
      continue;
    }
    wpd.push(dot(dist[q], PD));
    if (L.defQ !== null && q >= L.defQ) {
      dist.push(null); mult.push(null); spread.push(null); mark.push(RECOVERY);
      continue;
    }
    const pn = matVec(dist[q]);
    dist.push(pn);
    const m = dot(pn, MULT);
    const s = L.bsl[q] + L.basis * L.ai * m;
    mult.push(m); spread.push(s); mark.push(100 - (DUR * (s - s0)) / 100);
  }
  return { ...L, dist, wpd, mult, spread, mark, s0 };
});

const fmtDist = (p, top = 3) => {
  const idx = p.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, top);
  return idx.filter(([v]) => v >= 0.01).map(([v, i]) => `'${RATINGS[i]}':${Math.round(v * 100)}%`).join("  ");
};

// steps: 0 = setup; then per quarter: odd = draw (transition + copula), even = mark
const MAXSTEP = 8;
const stepInfo = (s) => (s === 0 ? { q: 0, phase: "setup" } : { q: Math.ceil(s / 2), phase: s % 2 === 1 ? "draw" : "mark" });

// stacked probability bar — the rating state each quarter
const DistBar = ({ p, dead, active }) => {
  if (dead)
    return (
      <div style={{ width: 54, height: 18, background: CRIMSON, borderRadius: 5, display: "flex",
        alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 800, fontSize: 11 }}>D</div>
    );
  const modal = p.indexOf(Math.max(...p));
  return (
    <div style={{
      width: 54, height: 18, borderRadius: 5, overflow: "hidden", display: "flex", position: "relative",
      outline: active ? `2px solid ${INK}` : "none", outlineOffset: 1,
    }}
      title={RATINGS.map((r, i) => `${r}: ${(p[i] * 100).toFixed(0)}%`).filter((_, i) => p[i] >= 0.005).join(" · ")}>
      {p.map((prob, i) =>
        prob < 0.005 ? null : <div key={i} style={{ width: `${prob * 100}%`, background: RC[RATINGS[i]], transition: "width .5s ease" }} />
      )}
      <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
        color: "#fff", fontWeight: 800, fontSize: 10.5, textShadow: "0 1px 2px rgba(0,0,0,.45)" }}>
        {RATINGS[modal]}
      </span>
    </div>
  );
};

const DrawStrip = ({ u, pd, fired }) => {
  const pdPct = Math.max(pd * 100, 1.2);
  return (
    <div style={{ width: "100%" }}>
      <div style={{ position: "relative", height: 14, background: "#E4EAF2", borderRadius: 7, overflow: "hidden" }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${pdPct}%`, background: fired ? CRIMSON : "#E7B7BF" }} />
        <div style={{ position: "absolute", top: 2, left: `calc(${Math.min(u * 100, 97)}% - 5px)`, width: 10, height: 10,
          borderRadius: "50%", background: fired ? CRIMSON : "#2E7D5B", border: "2px solid #fff",
          boxShadow: "0 1px 3px rgba(0,0,0,.3)", transition: "left .6s ease" }} />
      </div>
      <div style={{ fontSize: 10.5, fontFamily: "ui-monospace, monospace", marginTop: 3, color: fired ? CRIMSON : "#2E7D5B", fontWeight: 700 }}>
        U={u.toFixed(3)} {fired ? "<" : ">"} PD&#772; {(pd * 100).toFixed(1)}% {fired ? "→ DEFAULT" : "→ survives"}
      </div>
    </div>
  );
};

const SpreadBar = ({ L, q }) => {
  const s = L.spread[q], bsl = L.bsl[q];
  const scale = 100 / 1000;
  return (
    <div style={{ width: "100%" }}>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", background: "#E4EAF2" }}>
        <div style={{ width: `${bsl * scale}%`, background: NAVY, transition: "width .6s ease" }} />
        <div style={{ width: `${(s - bsl) * scale}%`, background: TEAL, transition: "width .6s ease" }} />
      </div>
      <div style={{ fontSize: 10.5, fontFamily: "ui-monospace, monospace", marginTop: 3, color: INK }}>
        {bsl.toFixed(0)} + {L.basis}×{L.ai.toFixed(2)}×{L.mult[q].toFixed(2)} = <b>{s.toFixed(0)}bp</b>
      </div>
    </div>
  );
};

export default function CLOSim() {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const { q, phase } = stepInfo(step);

  useEffect(() => {
    if (!playing) return;
    if (step >= MAXSTEP) { setPlaying(false); return; }
    const t = setTimeout(() => setStep((s) => s + 1), 2800);
    return () => clearTimeout(t);
  }, [playing, step]);

  const banner = phase === "setup"
    ? "Q0 SETUP — CIG A model gives rating.d0 (one-hot distribution), all marks at par"
    : phase === "draw"
      ? `Q${q} · STEP 1 — transition matrix diffuses the rating distribution: p(q) = p(q−1)·T · copula draw U vs weighted PD̄`
      : `Q${q} · STEP 2 — survivors remarked: BSL curve + basis × AI × Σ p(rating)·mult · defaults frozen at 1−LGD`;

  const worked = useMemo(() => {
    if (phase === "setup")
      return "Each quarter: (1) p(q) = p(q−1)·T — the rating diffuses across the scale, so a '6' becomes a mix like 78% '6', 12% '6-', 6% '5-', 3% '5'…  (2) copula draw U vs PD̄ = Σ p·PD → default?  (3) survivors: spread = BSL + basis×AI×Σp·mult → mark; defaults → 1−LGD = 45";
    const qi = q - 1;
    if (phase === "draw") {
      if (qi === 1) {
        const L = BOOK[3];
        return `Forge Industrial: entering-Q2 mix = ${fmtDist(L.dist[1])} → PD̄ = Σ p·PD = ${(L.wpd[1] * 100).toFixed(1)}%. Copula draw U = 0.011 < ${(L.wpd[1] * 100).toFixed(1)}% ⇒ DEFAULT. Value freezes at 1−LGD = ${RECOVERY.toFixed(0)}.`;
      }
      const L = BOOK[0];
      return `Alpha Energy: p(Q${q}) = p(Q${q - 1})·T → mix = ${fmtDist(L.dist[qi + 1])}. Copula draw U = ${L.u[qi].toFixed(2)} > PD̄ = ${(L.wpd[qi] * 100).toFixed(1)}% ⇒ survives.`;
    }
    const L = qi === 1 ? BOOK[2] : BOOK[0];
    return `${L.name}: mix = ${fmtDist(L.dist[qi + 1])} → weighted mult = Σ p·mult = ${L.mult[qi].toFixed(2)}. Spread = ${L.bsl[qi]} + ${L.basis}×${L.ai.toFixed(2)}×${L.mult[qi].toFixed(2)} = ${L.spread[qi].toFixed(0)}bp → mark = ${L.mark[qi].toFixed(1)} (S₀ = ${L.s0.toFixed(0)}bp).`;
  }, [step]);

  return (
    <div style={{ minHeight: "100vh", background: PAPER, color: INK, fontFamily: "-apple-system, 'Segoe UI', sans-serif", padding: 16 }}>
      <div style={{ maxWidth: 1000, margin: "0 auto" }}>
        <div style={{ fontFamily: "ui-monospace, monospace", fontWeight: 800, fontSize: 15, letterSpacing: 0.5 }}>
          PRIVATE CREDIT CLO · RATING / DEFAULT / SPREAD SIMULATION
        </div>
        <div style={{ color: SUBTLE, fontSize: 12.5, marginTop: 2 }}>
          Ratings evolve as probability distributions, not point ratings — each bar is the full rating mix (label = modal rating)
        </div>

        {/* rating legend */}
        <div style={{ display: "flex", gap: 10, marginTop: 8, fontSize: 11, color: SUBTLE, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 700 }}>rating mix:</span>
          {RATINGS.map((r) => (
            <span key={r} style={{ display: "flex", alignItems: "center", gap: 3 }}>
              <span style={{ width: 10, height: 10, background: RC[r], borderRadius: 2, display: "inline-block" }} />{r}
            </span>
          ))}
          <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <span style={{ width: 10, height: 10, background: CRIMSON, borderRadius: 2, display: "inline-block" }} />default
          </span>
        </div>

        {/* quarter tracker + controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          {[0, 1, 2, 3, 4].map((i) => (
            <button key={i} onClick={() => { setPlaying(false); setStep(i === 0 ? 0 : i * 2); }}
              style={{ border: "none", borderRadius: 8, padding: "5px 11px", fontWeight: 700, cursor: "pointer",
                background: i === q ? INK : "#DDE3EB", color: i === q ? "#fff" : SUBTLE, fontSize: 12 }}>Q{i}</button>
          ))}
          <div style={{ flex: 1 }} />
          <button onClick={() => { setPlaying(false); setStep(Math.max(0, step - 1)); }}
            style={{ border: `1px solid ${LINE}`, background: PANEL, borderRadius: 8, padding: "5px 11px", cursor: "pointer", fontSize: 12 }}>◀ Back</button>
          <button onClick={() => setPlaying((p) => !p)}
            style={{ border: "none", background: TEAL, color: "#fff", borderRadius: 8, padding: "5px 13px", cursor: "pointer", fontWeight: 700, fontSize: 12 }}>
            {playing ? "Pause" : "▶ Play"}
          </button>
          <button onClick={() => { setPlaying(false); setStep(Math.min(MAXSTEP, step + 1)); }}
            style={{ border: `1px solid ${LINE}`, background: PANEL, borderRadius: 8, padding: "5px 11px", cursor: "pointer", fontSize: 12 }}>Next ▶</button>
        </div>

        <div style={{ background: "#DDE6F2", borderRadius: 10, padding: "8px 12px", marginTop: 10, fontWeight: 700, fontSize: 12.5 }}>
          {banner}
        </div>

        {/* loan rows */}
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          {BOOK.map((L) => {
            const qi = q - 1;
            const deadRow = L.defQ !== null && qi >= L.defQ && phase === "mark";
            return (
              <div key={L.name} style={{
                background: PANEL, border: `1px solid ${LINE}`, borderRadius: 12, padding: "10px 14px",
                display: "grid", gridTemplateColumns: "160px 1fr 240px 86px", gap: 14, alignItems: "center",
              }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13, color: deadRow ? GRAYED : INK }}>{L.name}</div>
                  <div style={{ fontSize: 11, color: SUBTLE }}>{L.ind}</div>
                  <div style={{ fontSize: 10.5, color: SUBTLE, fontFamily: "ui-monospace, monospace" }}>
                    AI ×{L.ai.toFixed(2)} · basis {L.basis}bp
                  </div>
                </div>

                {/* distribution path Q0..q */}
                <div style={{ display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
                  {Array.from({ length: q + 1 }, (_, k) => {
                    if (L.defQ !== null && k > L.defQ + 1) return null;
                    const dead = L.defQ !== null && k === L.defQ + 1;
                    if (!dead && L.dist[k] === null) return null;
                    return (
                      <React.Fragment key={k}>
                        {k > 0 && <span style={{ color: SUBTLE, fontSize: 11 }}>→</span>}
                        <DistBar p={dead ? null : L.dist[k]} dead={dead} active={k === q && phase !== "setup"} />
                      </React.Fragment>
                    );
                  })}
                </div>

                {/* middle: copula strip or spread bar */}
                <div>
                  {phase === "setup" && (
                    <div style={{ fontSize: 11, color: SUBTLE }}>
                      rating.d0 = {L.r0} · PD({L.r0}) = {(PD[RATINGS.indexOf(L.r0)] * 100).toFixed(1)}%/qtr
                    </div>
                  )}
                  {phase === "draw" && (
                    L.u[qi] != null
                      ? <DrawStrip u={L.u[qi]} pd={L.wpd[qi]} fired={L.defQ === qi} />
                      : <div style={{ fontSize: 11, color: GRAYED, fontStyle: "italic" }}>defaulted — out of simulation</div>
                  )}
                  {phase === "mark" && (
                    L.spread[qi] === null
                      ? <div style={{ fontSize: 12, fontFamily: "ui-monospace, monospace", color: CRIMSON, fontWeight: 700 }}>
                          value = 1 − LGD = {RECOVERY.toFixed(0)}
                        </div>
                      : <SpreadBar L={L} q={qi} />
                  )}
                </div>

                {/* mark */}
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 20, fontWeight: 800, color: phase === "mark" && L.spread[qi] === null ? CRIMSON : phase === "setup" ? GRAYED : INK }}>
                    {phase === "setup" ? "100.0" : phase === "draw"
                      ? (qi === 0 ? "100.0" : L.mark[qi - 1].toFixed(1))
                      : L.mark[qi].toFixed(1)}
                  </div>
                  <div style={{ fontSize: 10, color: SUBTLE }}>
                    {phase === "mark" && L.spread[qi] === null ? "recovery" : "mark"}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* worked example */}
        <div style={{ background: "#E4EAF2", borderRadius: 12, padding: "10px 14px", marginTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 800, color: SUBTLE, letterSpacing: 1 }}>WORKED EXAMPLE</div>
          <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 12, marginTop: 4, lineHeight: 1.5,
            color: phase === "draw" && q === 2 ? CRIMSON : INK }}>{worked}</div>
        </div>

        {/* footer legend */}
        <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11, color: SUBTLE, flexWrap: "wrap" }}>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: NAVY, borderRadius: 2, marginRight: 4 }} />BSL industry curve</span>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: TEAL, borderRadius: 2, marginRight: 4 }} />basis × AI × weighted rating mult</span>
          <span>hover a rating bar to see the full probability mix · LGD {Math.round(LGD * 100)}% · duration {DUR} · illustrative numbers</span>
        </div>
      </div>
    </div>
  );
}

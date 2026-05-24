# Code Access Security (코드 접근 보안)

## Current State Assessment

### High Risk
- **GitHub Repository is Public**
  - Full trading strategy source code is publicly accessible:
    - `engines/score_engine.py` (7-layer scoring logic)
    - `engines/technical.py`, `regime.py`, `correlation.py`, `time_window.py`, `risk_manager.py`
  - Anyone can clone the repo and replicate or reverse-engineer the exact signals.

- **Frontend Code Fully Readable**
  - After successful Google login, "View Source" reveals the entire client-side application in one `index.html` (~85KB).
  - Variable names, layer weights (if any in JS), rendering logic, and data handling are visible.

### Medium / Low Risk
- No secrets (Alpaca keys, FlashAlpha key, Google Client ID) are hardcoded in source. All use `os.getenv()` + Vercel Environment Variables. Good.
- Backend Python code (`api/data.py` + engines) is **not** directly downloadable from the live site (Vercel serverless functions do not expose source).
- Authentication is now very strict (Google SSO + per-refresh + per-back-button enforcement).

## Prioritized Recommendations

### 1. Make the GitHub Repository Private (Immediate Action - Highest Impact)
1. Go to the repo on GitHub → **Settings → General**
2. Scroll to "Danger Zone"
3. Click **"Change repository visibility"** → **Private**
4. Confirm

This single change hides the core IP from the public internet.

After making it private:
- Remove any public forks if they exist.
- Only invite trusted team members as collaborators.

### 2. Add Frontend Build Pipeline + Obfuscation (Next Step)
Current single-file `index.html` makes reverse engineering too easy.

Recommended:
- Use **Vite** (or esbuild) to bundle + minify.
- Add **javascript-obfuscator** or **terser** with aggressive options for the production bundle.
- Output a single (or few) heavily mangled JS file(s).
- Deploy only the built artifacts to Vercel.

I can implement a basic `vite.config.js` + build script if you approve.

### 3. Move Proprietary Logic & Constants to Backend
- Any "magic numbers", exact layer weights, special filters, or proprietary indicators should live only in `api/data.py` or environment variables.
- Never put them in client-side JS where they can be inspected.

### 4. Additional Hardening Options
- **Vercel Edge Middleware** + IP allowlist on top of Google login (for extra protection on the most sensitive users).
- Short-lived JWTs instead of simple session cookies (more complex but stronger).
- Add request signing or additional per-request tokens for the `/api/data` endpoint.
- Regular audit of `ALLOWED_EMAILS` in Vercel.

### 5. Repository Hygiene
- The current `.gitignore` is decent.
- Consider splitting the repo in the future:
  - Public repo: only the deployed `index.html` + `api/` (without full engines source).
  - Private repo: full `engines/` + backtesting scripts + research.

## What Is Already Good

- Strong per-access authentication (Google + forced re-login on refresh/back).
- Proper secret management via environment variables.
- No obvious credential leaks in the codebase.
- Server-side rate limiting present.

## Action Items for You

- [ ] Make GitHub repo **Private** today.
- [ ] Decide whether to proceed with a proper frontend build + obfuscation pipeline (I can build it).
- [ ] Review the most sensitive parts of `score_engine.py` and `technical.py` — decide what must stay private.

---

**Last reviewed**: 2026-05-23  
**Owner**: Yoon

If you want, reply with "1" (make repo private advice), "2" (implement frontend build/obfuscation), or "3" (both) and I'll execute the corresponding changes.
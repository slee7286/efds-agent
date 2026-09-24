# Connect the EFDS agent to the live site

As checked on 24 September 2026, a public `POST /api/chat` request to the live
EFDS site returned HTTP 503, `Agent service is not configured`. The site's
`EFDS_AGENT_URL` is unset. The agent has a root `app.py` ASGI entrypoint for a
**separate** Vercel Python project. Vercel's [FastAPI guide](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel)
and [Python runtime reference](https://vercel.com/docs/functions/runtimes/python)
document automatic FastAPI detection and streaming responses. This repository
has not yet been connected to a Vercel project or deployed there.

## One-time project setup

1. In the same trusted Vercel workspace that hosts `efds-site`, import the
   GitHub repository `slee7286/efds-agent` as a new project. Use the repository
   root and leave build/output overrides empty. Do not combine it with the
   existing Next.js site project.
2. Set these **server-only** environment variables for Production before the
   first deployment. Enter key values in Vercel, never in Git or chat:

   | Name | Value/source |
   | --- | --- |
   | `APP_ENV` | `production` |
   | `SUPABASE_URL` | `https://immldithmugfrpojetmm.supabase.co` |
   | `SUPABASE_PUBLISHABLE_KEY` | The publishable key for that same Supabase project. A legacy JWT `SUPABASE_ANON_KEY` works if still active. Never use a service-role/secret key. |
   | `AI_API_KEY` | An OpenAI API key controlled by the EFDS operator. Set a project spend limit in OpenAI before enabling public chat. |
   | `AGENT_SHARED_SECRET` | A new, long random value shared only with the site server. Required in production. |
   | `ALLOWED_ORIGINS` | `https://www.imperial-efds.com` |

   The agent's token budget is process-local; it is **not** an organization-wide
   spend cap on Vercel's multiple function instances. The service secret blocks
   direct unauthenticated agent calls, but the public site's proxy remains
   callable, so use provider-side usage controls and monitor traffic. A shared
   production rate limiter is still a separate follow-up.

3. Deploy the new agent project from `main`. Record its HTTPS project URL.
   If the Vercel dashboard cannot trigger the deployment, these Bash commands
   link the **existing** project and deploy the checked-out `main` after you
   authenticate in your own browser. Confirm the selected project name in the
   `link` prompt; no credentials are passed on the command line:

   ```bash
   cd /home/siheon/projects/efds-agent
   git status --short --branch
   npx --yes vercel@latest login
   npx --yes vercel@latest link
   npx --yes vercel@latest --prod
   ```

   From a Linux/WSL shell, these read-only checks reveal configuration status
   without returning source records:

   ```bash
   cd /home/siheon/projects/efds-agent
   agent_url='https://<YOUR-AGENT-PROJECT>.vercel.app'
   curl -fsS "$agent_url/health" | python3 -m json.tool
   curl -fsS "$agent_url/v1/health/retrieval" | python3 -m json.tool
   ```

   Replace `<YOUR-AGENT-PROJECT>`. `/health` should report both retrieval and
   model as configured. The second endpoint should report `status: ready` for
   the canonical retrieval RPC. It probes without exposing evidence. A green
   health check does not prove every role's answer quality or citation flow.

4. In the **existing** Vercel `efds-site` project, set Production
   `EFDS_AGENT_URL` to that agent URL and `EFDS_AGENT_SHARED_SECRET` to the
   **same** value. Redeploy the site's current `main`. Do not prefix either
   variable with `NEXT_PUBLIC_`.
5. Verify the public proxy with a non-sensitive question. HTTP 200 plus an
   SSE `done` event proves transport; an insufficient-evidence answer is valid
   if no public source has been published:

   ```bash
   cd /home/siheon/projects/efds-site
   curl -sS --max-time 30 -N -H 'Content-Type: application/json' \
     -X POST 'https://www.imperial-efds.com/api/chat' \
     --data '{"message":"What is EFDS?","scope":"public","source_mode":"preterm_knowledge","conversation":[]}'
   ```

After the public path works, use real designated member, committee and admin
sessions to verify the scope boundary and the ticket-suggestion flow. Do not
send account passwords or session tokens to another person. Keep the agent's
project URL and deployment reference in the platform handoff, but never its
API key or service secret.

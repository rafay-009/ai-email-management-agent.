# Gmail and OpenAI setup

1. Create a Google Cloud project and enable Gmail API. Configure a consent screen and add a dedicated test account if the app is in testing.
2. Create an OAuth web client. Register the exact callback `http://localhost:8000/api/v1/auth/google/callback` for local development.
3. Copy `.env.example` to `.env`. Fill `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Generate separate keys using the backend virtual environment:

   ```sh
   cd backend
   .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
   .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Use the first value for `SESSION_SECRET` and the second for `TOKEN_ENCRYPTION_KEY`. Restart the backend.
4. Select **Connect Gmail**, grant read-only access, and select **Sync inbox**. Up to 50 recent inbox messages are retrieved per sync.
5. Grant **sending access** or **mailbox changes** in Settings if needed. Google treats mailbox modification as a broader scope that also permits sending. External actions still require review and approval in the app.

Google can require consent verification or a security assessment for public use of Gmail scopes. This project has no Google verification. [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server) · [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) · [Sending guide](https://developers.google.com/workspace/gmail/api/guides/sending).

## OpenAI

Set `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. The documented `gpt-4.1-mini` model is an example that supports Structured Outputs. Account access depends on your API project. In the live workspace, opt in through **Settings → Assistant preferences**. Analysis submits bounded selected email and thread text plus related task titles, and drafting includes the selected tone. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) · [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

A manual smoke test requires explicit authorization and a dedicated mailbox. Verify read-only sync, unexpected permissions, and disconnected behavior first. If authorized to send, review one draft addressed to a controlled recipient, execute the approval once, and confirm threading in Gmail. No real email is sent by the automated suite.

# Security

Report vulnerabilities privately to the repository owner; do not open a public issue containing an
exploit, credential, private media, or consent-sensitive reference.

The default API bind is localhost. Do not expose it publicly without configuring authentication,
trusted origins, upload limits, and a reverse proxy. Provider CLI commands are administrator-owned
argument arrays and must never use shell evaluation. Treat workflow templates, model files, uploads,
and generated media as untrusted input and keep secrets outside the repository.

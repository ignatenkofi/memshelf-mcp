# POLICY — PII & redaction rules for this shelf

Episodes are records of past conversations; before anything is written to
this shelf:

1. Credential-shaped strings (tokens, keys, `.env` assignments, bearer
   headers) are replaced with `«redacted:<kind>»`. The shelve tool does this
   mechanically; treat it as a safety net, not permission to paste secrets.
2. No personal identifiers of third parties — names, emails, handles,
   profile links. Use stable neutral labels («person A», roles) instead.
   <!-- Extend with your domain's rules, e.g. a course shelf: student
        names/nicknames are forbidden; roles and codes only. -->
3. Raw transcripts and import source material are input only: they are never
   copied onto the shelf and never committed anywhere.
4. One-off addressed artifacts (feedback for a specific person) are
   referenced generically, never stored verbatim.

`memshelf doctor` scans for secret shapes at rest; a finding blocks the push
until resolved. Machine-readable domain rules go in `POLICY.patterns`.

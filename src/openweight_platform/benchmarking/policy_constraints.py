"""Versioned output constraints for policy benchmark responses."""


POLICY_DECISION_CONSTRAINT_NAME = "policy-decision-v1"

# Muse Glimmer generates an ATEM message envelope before llama.cpp extracts
# ``reasoning_content`` and final ``content``. The grammar therefore constrains
# the raw completion: zero or more private analysis messages, followed by one
# final message containing only a decision accepted by ``parse_decision``.
# The initial ``<|start|>assistant`` is already supplied by the chat template.
MUSE_POLICY_DECISION_GBNF = r'''root ::= (analysis <|start|> "assistant")* final

analysis ::= " to=self" <|message|> (!<|eom|>)* <|eom|>
final ::= (" to=user")? <|message|> policy-ws decision policy-ws end
decision ::= approve | deny | needs-info
approve ::= [Aa] [Pp] [Pp] [Rr] [Oo] [Vv] [Ee]
deny ::= [Dd] [Ee] [Nn] [Yy]
needs-info ::= [Nn] [Ee] [Ee] [Dd] [Ss] "_" [Ii] [Nn] [Ff] [Oo]
policy-ws ::= [\x09-\x0D\x1C-\x20\u0085\u00A0\u1680\u2000-\u200A\u2028-\u2029\u202F\u205F\u3000]*
end ::= <|eot|> | <|eom|>'''


__all__ = [
    "MUSE_POLICY_DECISION_GBNF",
    "POLICY_DECISION_CONSTRAINT_NAME",
]

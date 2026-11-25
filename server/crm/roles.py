"""Shared CRM role vocabulary; usable in the offline Windows runtime."""
CAPABILITIES = frozenset(['call', 'visit', 'delivery', 'note', 'contact', 'promise', 'task',
                         'reported_payment', 'document', 'correct', 'shift_correct',
                         'analytics', 'exchange', 'accounting', 'assign'])
ROLE_CAPABILITIES = {
    'employee': CAPABILITIES - {'delivery', 'shift_correct', 'analytics', 'exchange', 'accounting', 'assign'},
    'courier': frozenset({'delivery', 'task', 'correct'}),
    'logistic': frozenset({'logistics'}),
    'manager': CAPABILITIES,
    'analyst': frozenset({'note', 'contact', 'document', 'correct', 'analytics',
                          'exchange', 'accounting', 'shift_correct'}),
    'observer': frozenset({'analytics'}),
}
GRANT_ROLE_CAPABILITIES = dict(ROLE_CAPABILITIES,
    manager=ROLE_CAPABILITIES['manager'] | {'logistics'},
    owner=CAPABILITIES | {'logistics'},
)

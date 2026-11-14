"""Shared CRM role vocabulary; usable in the offline Windows runtime."""
CAPABILITIES = frozenset(['call', 'visit', 'delivery', 'note', 'contact', 'promise', 'task',
                         'reported_payment', 'document', 'correct', 'shift_correct',
                         'analytics', 'exchange', 'accounting', 'assign'])
ROLE_CAPABILITIES = {
    'employee': CAPABILITIES - {'delivery', 'shift_correct', 'analytics', 'exchange', 'accounting', 'assign'},
    'courier': frozenset({'delivery', 'task', 'correct'}),
    'manager': CAPABILITIES,
    'analyst': frozenset({'note', 'contact', 'document', 'correct', 'analytics',
                          'exchange', 'accounting', 'shift_correct'}),
    'observer': frozenset({'analytics'}),
}

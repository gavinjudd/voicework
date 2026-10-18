"""HTTP review boundary; AI execution can collect context but cannot approve it."""
from .identity import _fields
from .live import LIVE_PROVIDERS


class IdentityRoutes:
    def __init__(self, store):
        self.store = store

    def route(self, handler, path, query, body):
        method = 'GET' if handler.command == 'HEAD' else handler.command
        if path == '/api/identities':
            if method == 'GET':
                return handler._json({'profiles': self.store.list()}) or True
            if method == 'POST':
                return handler._json({'profile': self.store.create(body)}, 201) or True
        if path == '/api/identities/resolve' and method == 'POST':
            _fields(body, ('context',))
            return handler._json(self.store.resolve(body.get('context'))) or True
        parts = path.split('/')
        if len(parts) == 4 and parts[1:3] == ['api', 'identity-jobs'] and method == 'GET':
            return handler._json(handler.server.identity_jobs.get(parts[3])) or True
        if len(parts) == 4 and parts[1:3] == ['api', 'identity-proposals'] and method == 'POST':
            return handler._json(self.store.review(parts[3], body)) or True
        if len(parts) == 4 and parts[1:3] == ['api', 'identity-sources'] and method == 'PATCH':
            _fields(body, ('withdrawn',))
            return handler._json(self.store.withdraw_source(parts[3], body.get('withdrawn'))) or True
        if len(parts) >= 4 and parts[1:3] == ['api', 'identities']:
            ident = parts[3]
            if len(parts) == 4:
                if method == 'GET':
                    return handler._json({'profile': self.store.get(ident), 'sources': self.store.sources(ident, False),
                        'proposals': self.store.proposals(ident), 'history': self.store.history(ident)}) or True
                if method == 'PATCH':
                    return handler._json({'profile': self.store.update(ident, body)}) or True
            if len(parts) == 5:
                if parts[4] == 'sources':
                    if method == 'GET':
                        return handler._json({'sources': self.store.sources(ident)}) or True
                    if method == 'POST':
                        return handler._json({'source': self.store.intake(ident, body)}, 201) or True
                if parts[4] == 'intake-plan' and method == 'GET':
                    return handler._json(self.store.intake_plan(ident)) or True
                if parts[4] == 'infer' and method == 'POST':
                    _fields(body, ('source_ids',))
                    if handler.server.provider.mode not in LIVE_PROVIDERS:
                        raise ValueError('Identity inference needs a live provider. Your context remains saved; edit the profile manually or select a live provider')
                    prepared = self.store.prepare(ident, body.get('source_ids'))
                    provider = handler.server.provider
                    def operation():
                        name = provider.mode + '/' + str(provider.model)
                        cached = self.store.cached_inference(prepared, name)
                        if cached is not None:
                            return cached
                        output = provider._invoke('identity', prepared)
                        return self.store.save_inference(prepared, output, name)
                    try:
                        job = handler.server.identity_jobs.start(operation, 'identity')
                    except RuntimeError:
                        return handler._json({'error': 'Generation is busy. Your context is saved; try again shortly.'}, 429) or True
                    return handler._json(job, 202) or True
        return False

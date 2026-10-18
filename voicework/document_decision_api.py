"""Explicit HTTP actions for document-only kept wording; no implicit learning."""
from .documents import validate_document_text
from .learning import validate_context


class DocumentDecisionRoutes:
    def __init__(self, store):
        self.store = store

    def route(self, handler, path, body, controls_validator):
        if handler.command != 'POST' or not path.startswith('/api/document-decisions'):
            return False
        if path in ('/api/document-decisions', '/api/document-decisions/resolve'):
            keeping = path == '/api/document-decisions'
            handler._fields(body, {'source', 'text', 'context', 'controls'} | ({'suggestion', 'reason'} if keeping else set()))
            source = validate_document_text(body.get('source'))
            draft = validate_document_text(body.get('text'), output=True)
            context = validate_context(body.get('context'))
            controls = controls_validator(body.get('controls'), document=True)
            if keeping:
                decision = self.store.keep(source, draft, context, controls, body.get('suggestion'), body.get('reason'))
                return handler._json({'decision': decision, 'decisions': self.store.resolve(source, draft, context, controls)}) or True
            return handler._json(self.store.resolve(source, draft, context, controls)) or True
        pieces = path.split('/')
        if len(pieces) == 5 and pieces[4] == 'revoke' and len(pieces[3]) == 32:
            handler._fields(body, set())
            return handler._json({'decision': self.store.revoke(pieces[3])}) or True
        raise KeyError(path)


class DocumentWordingRoutes:
    def __init__(self, store):
        self.store = store

    def route(self, handler, path, body, controls_validator):
        if handler.command != 'POST' or not path.startswith('/api/document-wording'):
            return False
        if path in ('/api/document-wording', '/api/document-wording/resolve'):
            saving = path == '/api/document-wording'
            handler._fields(body, {'source', 'context', 'controls'} | ({'source_quote', 'wording', 'reason'} if saving else set()))
            source = validate_document_text(body.get('source'))
            context = validate_context(body.get('context'))
            controls = controls_validator(body.get('controls'), document=True)
            if saving:
                correction = self.store.correct(source, context, controls, body.get('source_quote'), body.get('wording'), body.get('reason'))
                return handler._json({'correction': correction, 'corrections': self.store.resolve(source, context, controls)}) or True
            return handler._json(self.store.resolve(source, context, controls)) or True
        pieces = path.split('/')
        if len(pieces) == 5 and pieces[4] == 'revoke' and len(pieces[3]) == 32:
            handler._fields(body, set())
            return handler._json({'correction': self.store.revoke(pieces[3])}) or True
        raise KeyError(path)

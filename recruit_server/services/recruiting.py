"""Compatibility facade composing the entity services for HTTP handlers."""
from recruit_server.services.applications import Applications
from recruit_server.services.candidates import Candidates
from recruit_server.services.requisitions import Requisitions


class Recruiting:
    def __init__(self, client):
        self.requisition_service = Requisitions(client)
        self.candidate_service = Candidates(client)
        self.application_service = Applications(client, self.candidate_service)

    def requisitions(self, *args, **kwargs):
        return self.requisition_service.requisitions(*args, **kwargs)

    def requisition(self, *args, **kwargs):
        return self.requisition_service.requisition(*args, **kwargs)

    def statuses(self, *args, **kwargs):
        return self.requisition_service.statuses(*args, **kwargs)

    def candidate(self, *args, **kwargs):
        return self.candidate_service.candidate(*args, **kwargs)

    def candidates(self, *args, **kwargs):
        return self.candidate_service.candidates(*args, **kwargs)

    def candidate_details(self, *args, **kwargs):
        return self.candidate_service.candidate_details(*args, **kwargs)

    def candidate_summary(self, *args, **kwargs):
        return self.candidate_service.candidate_summary(*args, **kwargs)

    def applications(self, *args, **kwargs):
        return self.application_service.applications(*args, **kwargs)

    def journey(self, *args, **kwargs):
        return self.application_service.journey(*args, **kwargs)

    def candidate_history(self, *args, **kwargs):
        return self.application_service.candidate_history(*args, **kwargs)

    def move(self, *args, **kwargs):
        return self.application_service.move(*args, **kwargs)


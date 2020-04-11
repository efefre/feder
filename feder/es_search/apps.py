from django.apps import AppConfig
from .settings import ELASTICSEARCH_URL
from elasticsearch_dsl import connections
from .documents import LetterDocument


class EsSearchConfig(AppConfig):
    name = "feder.es_search"

    def ready(self):
        connections.create_connection(hosts=ELASTICSEARCH_URL)

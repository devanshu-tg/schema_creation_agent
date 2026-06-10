from enum import Enum


class UseCase(str, Enum):
    FRAUD = "FRAUD"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    CUSTOMER_360 = "CUSTOMER_360"
    RECOMMENDATION = "RECOMMENDATION"
    # Autograph industry patterns (lightweight templates — fraud remains
    # the fully-fleshed reference)
    SUPPLY_CHAIN = "SUPPLY_CHAIN"
    CYBERSECURITY = "CYBERSECURITY"
    KNOWLEDGE_GRAPH = "KNOWLEDGE_GRAPH"


class DataKind(str, Enum):
    INT = "INT"
    FLOAT = "FLOAT"
    STRING = "STRING"
    DATETIME = "DATETIME"
    BOOL = "BOOL"
    CATEGORICAL = "CATEGORICAL"
    ID_LIKE = "ID_LIKE"


class Cardinality(str, Enum):
    UNIQUE = "UNIQUE"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EdgeDirection(str, Enum):
    DIRECTED = "DIRECTED"
    UNDIRECTED = "UNDIRECTED"
    DIRECTED_WITH_REVERSE = "DIRECTED_WITH_REVERSE"


class PIIClass(str, Enum):
    NONE = "NONE"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    NAME = "NAME"
    DOC_ID = "DOC_ID"
    IP = "IP"
    SSN = "SSN"
    CARD = "CARD"

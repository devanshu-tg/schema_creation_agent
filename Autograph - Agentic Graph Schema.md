# **Autograph — Agentic Graph Schema Design**

Unlike traditional schema builders that guide users through a predefined sequence of steps, Autograph operates as an autonomous graph architect.

Its objective is not to collect node, edge, and attribute definitions from users. Instead, Autograph starts with a business problem, investigates available data, identifies relevant entities and relationships, and designs a graph model optimized for the desired outcomes.

Autograph combines domain reasoning, data discovery, graph modeling expertise, and industry best practices to transform business requirements into production-ready graph architectures.

The interaction adapts dynamically based on:

* The business problem being solved  
* The available data sources  
* The quality and completeness of the data  
* The user's domain expertise  
* The agent's confidence in its recommendations  
* Industry-specific graph patterns and best practices

As a result, no two schema creation sessions are necessarily the same.

---

# **Core Agent Behaviors**

## **1\. Understand the Business Objective**

Autograph begins by understanding the business problem rather than asking users to think about graph structures.

### **Example Questions**

* What problem are you trying to solve?  
* What decisions do you want this system to help you make?  
* What outcomes are you hoping to improve?  
* Who will consume the graph: analysts, investigators, applications, AI agents, or data scientists?

### **Example User Goals**

* Detect payment fraud  
* Identify mule accounts  
* Build a Customer 360 platform  
* Improve supply chain visibility  
* Investigate cybersecurity threats  
* Build a GraphRAG knowledge system

Autograph focuses on business outcomes first and graph design second.

---

## **2\. Refine the Problem and Success Criteria**

Many business problems can be solved in multiple ways. Autograph works to understand the specific objectives that will influence the graph design.

For example, a fraud use case could involve:

* Payment fraud  
* Account takeover fraud  
* Synthetic identity detection  
* Mule account detection  
* Money laundering  
* Fraud ring investigation

### **Example Questions**

* Which fraud scenarios are most important?  
* Is the primary goal detection, investigation, explainability, or risk scoring?  
* Are you supporting human investigators, machine learning models, AI agents, or all three?  
* What business questions must the graph answer?

Autograph uses these answers to guide subsequent modeling decisions.

---

## **3\. Discover, Analyze, and Validate Data**

Once the objective is understood, Autograph automatically inspects available data sources and identifies the datasets most relevant to the problem.

Potential sources may include:

* Snowflake  
* Databricks  
* PostgreSQL  
* Salesforce  
* S3  
* Enterprise applications accessible through MCP

Autograph performs the following activities autonomously:

* Discover available datasets  
* Profile schemas and metadata  
* Analyze columns and keys  
* Identify candidate business entities  
* Detect relationships across systems  
* Assess data quality and completeness  
* Infer likely graph structures  
* Eliminate irrelevant datasets

### **Example Agent Output**

"I analyzed the available data sources and identified six datasets relevant to fraud detection:

* Transactions  
* Customers  
* Accounts  
* Devices  
* Login Activity  
* Merchant Data

These datasets contain the identifiers and relationships necessary to model customer behavior, account ownership, transaction flows, device sharing, and fraud patterns.

I excluded 27 datasets because they appear unrelated to the fraud detection objectives."

Autograph only requests user input when ambiguity exists.

### **Example Clarification Requests**

* I found two transaction datasets with overlapping information. Which is considered the system of record?  
* This field appears to contain a device fingerprint, but its meaning is undocumented. Can you confirm?  
* Customer identifiers appear in multiple systems. Should these represent the same customer entity?

The objective is to minimize user effort while maximizing autonomous discovery.

---

## **4\. Leverage Graph Design Patterns**

Autograph compares the discovered data model against common graph architectures and industry-specific reference patterns.

Examples include:

* Fraud Investigation Graph  
* Customer 360 Graph  
* Entity Resolution Graph  
* Supply Chain Graph  
* Cybersecurity Graph  
* Knowledge Graph  
* GraphRAG Graph

### **Example Agent Reasoning**

"Based on the discovered entities and relationships, this closely resembles a fraud investigation graph.

I recommend modeling Devices, IP Addresses, Merchants, and Transactions as first-class entities because they are commonly used to identify fraud rings, mule networks, and account takeover activity."

This allows Autograph to incorporate graph best practices rather than designing every schema from first principles.

---

## **5\. Infer the Graph Model**

Using the business objectives, discovered data, and graph design patterns, Autograph generates a proposed graph model.

The model may include:

* Business entities  
* Relationships  
* Attributes  
* Cardinality  
* Hierarchical structures  
* Temporal relationships  
* Event modeling strategies

Rather than asking users how to model the graph, Autograph makes recommendations and explains its reasoning.

### **Example Recommendation**

"I recommend modeling Customers, Accounts, Transactions, Devices, Merchants, and IP Addresses as separate entities.

This structure supports fraud investigations, fraud ring detection, account takeover analysis, and explainable AI workflows."

### **Example Clarification**

* Merchant activity appears important. Should merchant-level risk analysis be supported?  
* Do investigators need to analyze historical relationship changes over time?

Questions focus on business outcomes rather than graph implementation details.

---

## **6\. Explain the Design**

Autograph explains both the proposed schema and the reasoning behind it.

### **Example Explanation**

* Device and IP Address were modeled as entities because they enable identification of shared infrastructure across accounts.  
* Transactions were modeled as vertices because transaction-level traversal is important for fraud investigations.  
* Time-based attributes were preserved because fraud patterns frequently depend on sequence and timing.

This creates transparency and allows users to validate the agent's reasoning.

---

## **7\. Validate Business Outcomes**

Rather than asking users whether they like the schema, Autograph validates whether the graph can answer the intended business questions.

### **Example Agent Output**

"With this graph, you will be able to:

* Flag accounts sharing devices or IP addresses  
* Identify fraud rings and mule networks  
* Trace suspicious transaction flows  
* Investigate account takeover activity  
* Generate graph features for machine learning models  
* Support GraphRAG and AI-powered investigations"

### **Example Validation Questions**

* Are there additional questions this graph should answer?  
* Are there regulatory, audit, or compliance requirements that should be incorporated?  
* Are there future use cases we should design for now?

The discussion remains focused on outcomes rather than implementation.

---

## **8\. Present Recommendations and Confidence**

Before deployment, Autograph summarizes its findings, assumptions, and confidence level.

### **Example Agent Summary**

Recommended Entities:

* Customer  
* Account  
* Transaction  
* Device  
* Merchant  
* IP Address

Key Assumptions:

* transaction\_events is the system of record  
* Device fingerprints uniquely identify devices  
* Customer identifiers are consistent across systems

Expected Outcomes:

* Payment fraud detection  
* Mule account identification  
* Fraud ring discovery  
* Explainable investigations

Confidence Level:  
High

Potential Future Enhancements:

* Geospatial analysis  
* Merchant risk scoring  
* Graph neural network features  
* Real-time fraud detection

---

## **9\. Deploy and Operationalize**

Once validated, Autograph can generate and deploy graph assets automatically.

Capabilities include:

* Generate graph schema  
* Create loading jobs  
* Map source data  
* Load graph data  
* Validate data quality  
* Generate starter queries  
* Create graph analytics workflows  
* Recommend graph algorithms  
* Generate GraphRAG configurations  
* Create dashboards and investigation views

### **Example Deployment Questions**

* Would you like me to deploy the graph now?  
* Should I load a sample dataset first or the full production dataset?  
* Would you like me to generate starter fraud detection queries and analytics workflows?

The deployment process adapts based on infrastructure, governance requirements, and operational preferences.

---

# **Guiding Principle**

Autograph does not ask users how to build a graph.

Autograph investigates the business problem, analyzes the available data, applies graph expertise, recommends an optimal design, explains its reasoning, and only seeks user input when ambiguity or business-specific decisions require human judgment.

The result is a collaborative experience that feels less like schema creation and more like working with an experienced graph architect.


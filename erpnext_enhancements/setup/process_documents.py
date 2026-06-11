"""Version-controlled Process Document content (Mermaid.js process charts).

Entry point :func:`sync_process_documents` is registered in ``after_migrate``
(hooks.py). Every Process Document the business maintains is defined here and
upserted on each migrate: missing documents are created, and a document whose
``mermaid_code`` has drifted from the canonical text below is overwritten —
the repo is the source of truth for these charts, same philosophy as
fixtures/ (UI edits do not survive deploys). Documents created on the site
under titles *not* listed here are left alone, and nothing is ever deleted.

The first eleven charts were authored on the site (DB-only) and ported here
verbatim in v1.11.0; "Sapphire Fountains Enhancements Flow" is the app's own
chart — how erpnext_enhancements' custom subsystems (hand-off process steps,
field-service maintenance, the Time Kiosk, travel, integrations) extend the
stock ERPNext flows the other charts describe.

Rendering: the desk form script (public/js/process_document.js) renders
``mermaid_code`` with Mermaid 11, so stick to its flowchart syntax. Color
conventions shared across charts: CRM blue ``#E3F2FD``, buying orange
``#FFF3E0``, stock green ``#E8F5E9``, manufacturing purple ``#F3E5F5``,
accounting grey ``#ECEFF1``, projects cyan ``#E0F7FA``, HR red ``#FFEBEE``.

⚠️ Never put raw HTML in chart text — no ``<br/>`` line breaks, no ``<-->``
arrows. Frappe HTML-sanitizes the Markdown Editor field on save as soon as
the value looks like HTML, mangling every ``-->`` into ``--&gt;`` and
breaking the diagram (and the seeder's drift check would then rewrite the
document on every migrate). For multi-line labels use a quoted string
containing a real newline ("How to Order in ERPNext" does this throughout);
tests/test_process_documents.py enforces the no-``<`` rule.
"""

import frappe

PROCESS_DOCUMENTS = {
	# ------------------------------------------------------------------
	# Site-wide overview
	# ------------------------------------------------------------------
	"ERPNext Flow": """
graph TD
    %% Selling & CRM Cycle
    Lead --> |Convert| Opportunity
    Opportunity --> |Create| Accounts
    Opportunity --> |Create| Quotation
    Accounts --> |Select in| Quotation
    Quotation --> |Accept & Create| SalesOrder[Sales Order]
    SalesOrder --> |Fulfill via| DeliveryNote[Delivery Note]
    SalesOrder --> |Bill via| SalesInvoice[Sales Invoice]
    DeliveryNote --> |Get Items from| SalesInvoice

    %% Buying & Procurement Cycle
    MaterialRequest[Material Request] --> |Create| PurchaseOrder[Purchase Order]
    Supplier --> |Select in| PurchaseOrder
    PurchaseOrder --> |Receive via| PurchaseReceipt[Purchase Receipt]
    PurchaseOrder --> |Get Bill| PurchaseInvoice[Purchase Invoice]
    PurchaseReceipt --> |Bill against| PurchaseInvoice

    %% Inventory / Stock Module
    Item --> |Add to| SalesOrder
    Item --> |Add to| PurchaseOrder
    PurchaseReceipt --> |Increases Stock of| Item
    DeliveryNote --> |Decreases Stock of| Item
    StockEntry[Stock Entry] --> |Transfer/Manufacture| Item
    Warehouse --> |Is Location for| PurchaseReceipt
    Warehouse --> |Is Location for| DeliveryNote
    Warehouse --> |Is Location for| StockEntry

    %% Manufacturing Module
    SalesOrder --> |Can Trigger| WorkOrder[Work Order]
    BOM[Bill of Materials] --> |Is a Recipe for| WorkOrder
    Item --> |Is Made From| BOM
    WorkOrder --> |Consumes/Produces via| StockEntry
    WorkOrder --> |Generates Tasks via| JobCard

    %% Accounting Module (The Core)
    SalesInvoice --> |Creates Debits/Credits in| GLEntry[General Ledger Entry]
    PurchaseInvoice --> |Creates Debits/Credits in| GLEntry
    PaymentEntry[Payment Entry] --> |Reconciles & Creates| GLEntry
    JournalEntry[Journal Entry] --> |Manual Debits/Credits| GLEntry
    GLEntry --> |Updates Balance of an| Account
    SalesInvoice --> |Is Paid by| PaymentEntry
    PurchaseInvoice --> |Is Paid via| PaymentEntry
    Account --> |Organized in| ChartofAccounts[Chart of Accounts]

    %% Project Management Module
    SalesOrder --> |Can Create a| Project
    Project --> |Contains| Task
    Timesheet --> |Logs Time Against| Task

    %% Linking Projects & Accounting
    Timesheet --> |Billable Hours for| SalesInvoice
    ExpenseClaim[Expense Claim] --> |Can be Charged to| Project

    %% Human Resources (HR) Module
    Employee --> |Submits| LeaveApplication[Leave Application]
    Employee --> |Submits| ExpenseClaim
    ExpenseClaim --> |Posts accounting via| JournalEntry
    Employee --> |Receives| SalarySlip[Salary Slip]
    SalarySlip --> |Processed in bulk by| PayrollEntry[Payroll Entry]
    PayrollEntry --> |Posts accounting via| JournalEntry

    %% Style Definitions for Modules
    classDef crm fill:#E3F2FD,stroke:#2196F3,stroke-width:2px;
    classDef buying fill:#FFF3E0,stroke:#FF9800,stroke-width:2px;
    classDef inventory fill:#E8F5E9,stroke:#4CAF50,stroke-width:2px;
    classDef manufacturing fill:#F3E5F5,stroke:#9C27B0,stroke-width:2px;
    classDef accounting fill:#ECEFF1,stroke:#607D8B,stroke-width:2px;
    classDef projects fill:#E0F7FA,stroke:#00BCD4,stroke-width:2px;
    classDef hr fill:#FFEBEE,stroke:#F44336,stroke-width:2px;

    %% Class Assignments to Nodes
    class Lead,Opportunity,Accounts,Quotation,SalesOrder,SalesInvoice,DeliveryNote crm;
    class MaterialRequest,Supplier,PurchaseOrder,PurchaseInvoice,PurchaseReceipt buying;
    class Item,Warehouse,StockEntry inventory;
    class WorkOrder,BOM,JobCard manufacturing;
    class GLEntry,Account,ChartofAccounts,PaymentEntry,JournalEntry accounting;
    class Project,Task,Timesheet projects;
    class Employee,LeaveApplication,ExpenseClaim,SalarySlip,PayrollEntry hr;
""",
	# ------------------------------------------------------------------
	# Per-module flows
	# ------------------------------------------------------------------
	"Sales and CRM Flow in ERPNext": """
graph TD
    A[Lead] --> |Qualify| B[Opportunity]
    B --> |Create New| C[Customer]
    C --> |Has| D[Contact]
    C --> |Has| E[Address]
    B --> |Generate| F[Quotation]
    C --> |Add to| F
    F --> |Accept & Create| G[Sales Order]

    subgraph Handoffs
        G --> H[Delivery Note]
        G --> I[Sales Invoice]
        G --> J[Project]
    end

    %% Style Definitions
    classDef crm fill:#E3F2FD,stroke:#2196F3,stroke-width:2px;
    classDef handoff fill:#F5F5F5,stroke:#9E9E9E,stroke-width:2px,stroke-dasharray: 5 5;

    %% Class Assignments
    class A,B,C,D,E,F,G crm;
    class H,I,J handoff;
""",
	"Buying and Procurement ERPNext Flow": """
graph TD
    A[Material Request] --> |Request Quotes From| B[Supplier]
    B --> |Sends| C[Supplier Quotation]
    C --> |Compare & Create| D[Purchase Order]
    D --> |Receive Goods Against| E[Purchase Receipt]
    E --> |Updates Inventory Levels| F[Item Stock]
    D --> |Receive Bill| G[Purchase Invoice]
    E --> |Link to| G
    G --> |Schedule Payment| H[Payment Entry]

    %% Style Definitions
    classDef buying fill:#FFF3E0,stroke:#FF9800,stroke-width:2px;
    classDef stock fill:#E8F5E9,stroke:#4CAF50,stroke-width:2px;
    classDef accounting fill:#ECEFF1,stroke:#607D8B,stroke-width:2px;

    %% Class Assignments
    class A,B,C,D,E,G buying;
    class F stock;
    class H accounting;
""",
	"Inventory Flow in ERPNext Flow": """
graph TD
    subgraph "Stock In (Increases Inventory)"
        A[Purchase Receipt]
        B[Stock Entry - Material Receipt]
        C[Stock Entry - Manufacture Finish]
    end

    subgraph "Stock Out (Decreases Inventory)"
        D[Delivery Note]
        E[Stock Entry - Material Issue]
    end

    F[Item]
    G[Warehouse]
    H[Serial No]
    I[Batch]

    A & B & C --> |Increase Stock In| G
    D & E --> |Decrease Stock From| G
    G --> |Contains| F
    F --> |Can Have| H
    F --> |Can Have| I

    %% Style Definitions
    classDef stock fill:#E8F5E9,stroke:#4CAF50,stroke-width:2px;

    %% Class Assignments
    class F,G,H,I stock;
""",
	"Manufacturing or Build ERPNext Flow": """
graph TD
    A[Sales Order] --> |Plan Production| B[Production Plan]
    B --> |Generates| C[Work Order]
    D[Bill of Materials - BOM] --> |Is the 'Recipe' for| C
    C --> |Creates Tasks| E[Job Card]
    C --> |Issues Materials via| F[Stock Entry - Consume]
    C --> |Finishes Product via| G[Stock Entry - Manufacture]

    %% Style Definitions
    classDef manufacturing fill:#F3E5F5,stroke:#9C27B0,stroke-width:2px;
    classDef sales fill:#E3F2FD,stroke:#2196F3,stroke-width:2px;
    classDef stock fill:#E8F5E9,stroke:#4CAF50,stroke-width:2px;

    %% Class Assignments
    class B,C,D,E manufacturing;
    class A sales;
    class F,G stock;
""",
	"Accounting for ERPNext Flow": """
graph TD
    subgraph "Documents Creating Transactions"
        A[Sales Invoice]
        B[Purchase Invoice]
        C[Payment Entry]
        D[Journal Entry]
        E[Payroll Entry]
    end

    F[General Ledger Entry - GLE]
    G[Account]
    H[Chart of Accounts]

    A & B & C & D & E --> |Create| F
    F --> |Updates Balance of| G
    G --> |Is Organized In| H

    %% Style Definitions
    classDef accounting fill:#ECEFF1,stroke:#607D8B,stroke-width:2px;

    %% Class Assignments
    class A,B,C,D,E,F,G,H accounting;
""",
	"HR and Projects Flow in ERPNext": """
graph TD
    subgraph "Project Management"
        A[Sales Order] --> |Creates| B[Project]
        B --> |Contains| C[Task]
        D[Employee] --> |Logs Time via| E[Timesheet]
        E --> |Against| C
        E --> |For Billing| A
    end

    subgraph "Human Resources"
        D --> |Submits| F[Leave Application]
        D --> |Submits| G[Expense Claim]
        G --> |Can be linked to| B
        D --> |Is Assigned| H[Salary Structure]
        H --> |Generates| I[Salary Slip]
        I --> |Processed by| J[Payroll Entry]
    end

    %% Style Definitions
    classDef projects fill:#E0F7FA,stroke:#00BCD4,stroke-width:2px;
    classDef hr fill:#FFEBEE,stroke:#F44336,stroke-width:2px;
    classDef sales fill:#E3F2FD,stroke:#2196F3,stroke-width:2px;

    %% Class Assignments
    class B,C,E projects;
    class D,F,G,H,I,J hr;
    class A sales;
""",
	# ------------------------------------------------------------------
	# How-to / concept guides
	# ------------------------------------------------------------------
	"Lead to Project Flow in ERPNext": """
graph TD
    A[Lead] -->|Convert| B[Opportunity]
    B -->|Create| C[Quotation]
    C -->|On Acceptance| D[Sales Order]
    D -->|To Fulfill| E[Project]
""",
	"How to Order in ERPNext": """
graph TD
    subgraph "Procurement Workflow"
        direction TB
        A["**Step 1: Material Request**
        (Internal 'Ask')
        *Optional internal document to signal a need.*"]
        B["**Step 2: Purchase Order**
        (External 'Order')
        *Formal, legally binding contract sent to a supplier.*"]
        C["**Step 3: Purchase Receipt**
        (Inventory 'Receiving')
        *Confirms physical receipt of items and increases stock levels.*"]
        D["**Step 4: Purchase Invoice**
        (Supplier 'Bill')
        *Records the financial liability (Accounts Payable).*"]
        E["**Step 5: Payment Entry**
        (Final 'Payment')
        *Documents outgoing payment and closes the payable.*"]
    end

    Start((Start)) -- "Create PO Directly
    *(Skips internal request)*" --> B
    A -- "Create PO from Request" --> B
    B -- "Receive Goods Against Order" --> C
    B -- "Create Bill for Prepayment
    *(Pay before receiving)*" --> D
    C -- "Create Bill After Receiving
    *(Most common path)*" --> D
    D -- "Make Payment Against Invoice" --> E
    E --> End((End))

    style A fill:#e3f2fd,stroke:#333,stroke-width:2px
    style B fill:#e8f5e9,stroke:#333,stroke-width:2px
    style C fill:#fff3e0,stroke:#333,stroke-width:2px
    style D fill:#fce4ec,stroke:#333,stroke-width:2px
    style E fill:#f3e5f5,stroke:#333,stroke-width:2px
""",
	"Document Linking in ERPNext": """
graph TD
    A[Start: User opens Target Document] --> B{Clicks Get items from...};
    B --> C[Dialog appears to select Source];
    C --> D[User selects Source Document];
    D --> E[System fetches data from Source];
    E --> F[Target Document fields are populated];
    F --> G[Dynamic Link is created];
    G --> H[Target stores Source name in Link Field];
    G --> I[Source dashboard is updated];
    I --> J{Source status is updated};
    J --> K[End: Documents are linked];

    style A fill:#D6EAF8,stroke:#3498DB
    style K fill:#D5F5E3,stroke:#2ECC71
    style B fill:#FCF3CF,stroke:#F1C40F
    style J fill:#FDEDEC,stroke:#E74C3C
""",
	"Customer Management": """
graph TD
    subgraph "1. Market Definition"
        A["Market/Customer Profiles"]
        B[("Marketing Group")]
        A -- "Created & Maintained by" --> B
    end

    subgraph "2. Lead & Prospect Generation"
        C{Leads}
        D{Prospects}
        E[("Sales Development Rep")]
        F[("Market Response Rep")]
    end

    subgraph "3. Sales Execution"
        G{Opportunities}
        H[("Account Executive")]
    end

    subgraph "4. Customer Relationship"
        I[Clients]
        J[Champions]
        K[("Account Manager")]
    end

    %% Connections
    A -- "Identifies" --> C
    C -- "Positive Response to Outreach" --> D
    D -- "Engaged by" --> E & F
    E -- "Qualifies into" --> G
    F -- "Qualifies into" --> G
    G -- "Transitioned to" --> H
    H -- "Sells & Closes Business" --> I
    I -- "Maintained by" --> K
    I -- "Refer Business/Offer Testimonial" --> J
""",
	# ------------------------------------------------------------------
	# This app's custom processes
	# ------------------------------------------------------------------
	"Sapphire Fountains Enhancements Flow": """
graph TD
    %% How the ERPNext Enhancements app (erpnext_enhancements) layers
    %% Sapphire Fountains' processes onto the stock ERPNext flows the other
    %% Process Documents describe. Solid arrows are document flows; dotted
    %% arrows are background syncs and integrations.

    subgraph SALES["Sales & Project Hand-Off"]
        LEAD[Lead] --> |Qualify| OPP[Opportunity]
        OPP --> |"Sales Pipeline kanban
        (stage stamps, weighted totals)"| WON{Closed Won}
        WON --> |"Custom Create Project
        (attachments carry over)"| PROJ[Project]
        PROJ --> |"Seeded on insert from
        Process Step Templates"| STEPS["Hand-Off Process Steps
        (7-step tracker)"]
        STEPS --> |"Anchored steps auto-complete: Opportunity Won /
        Project Created / Payment Received"| CURR["Current step (SLA due-by)"]
        CURR --> |"SMS + Notification on each hand-off,
        daily escalation when overdue"| RESP["Responsible person
        (PM / AE / AR rep)"]
        PROJ --> |Contains| TASK[Task]
        TASK --> |"Recurring tasks generate
        their next occurrence"| TASK
    end

    subgraph MAINT["Field-Service Maintenance"]
        SO[Sales Order] --> |Create| MC[Maintenance Contract]
        PCON["Project Contract
        (signed services agreement)"] --> |Create| MC
        MC --> |"Daily predictive scheduler drafts visits
        (per feature / per site / seasonal)"| MRD["Maintenance Record (Draft)"]
        MRD --> |"Technician fills template-driven form
        (checklists, chemistry, dosing, photos)"| MRS["Maintenance Record (Submitted)"]
        MRS --> |Out-of-range chemistry alerts| SUP[Maintenance Supervisor]
        MRS --> |Customer portal| PORTAL["/maintenance-records"]
    end

    subgraph KIOSK["Time Tracking (Time Kiosk PWA)"]
        TECH[Technician] --> |"Clock in at /kiosk
        (nearest-site picker, offline queue)"| JI["Job Interval (session)"]
        JI --> |Batched GPS points| TKL[Time Kiosk Log]
        TKL --> |Replayed on| MAP[Location Timeline map]
    end

    subgraph TRAVEL["Travel Management"]
        TT[Travel Trip] --> |"Workflow: Draft, Requested, Approved,
        Booking, Travel, Expense Review, Closed"| ECL["Expense Claim (Draft)"]
    end

    subgraph ACCT["Stock ERPNext Hand-Off"]
        SE["Stock Entry
        (Material Issue)"]
        WCL[Warranty Claim]
        SINV["Sales Invoice (Draft)"]
        TS[Timesheet]
        GL[General Ledger]
        SINV --> GL
        TS --> |Billable hours| SINV
        ECL --> |Posts via Journal Entry| GL
    end

    subgraph EXT["External Integrations"]
        GDRIVE[("Google Drive")]
        QBO[("QuickBooks Online")]
        TRITON[("Triton Gateway
        (Twilio + AI)")]
        GCAL[("Google Calendar")]
        GA4[("GA4 + Search Console")]
        MCPT[("AI Assistants via MCP")]
    end

    %% Cross-subsystem hand-offs
    PROJ --> |"Create (verbal / legacy arrangement)"| MC
    JI --> |"Maintenance Form button when the project
    has an Active contract or template"| MRD
    JI --> |"sync_time_kiosk consolidation"| TS
    MRS --> |Consumed chemicals| SE
    MRS --> |Failed in-warranty checks| WCL
    MRS --> |Per-visit billing| SINV
    MRS --> |Visit hours auto-fill| TS

    %% Background syncs & integrations
    WON -.-> |Provisions project folder tree| GDRIVE
    GL -.-> |"Pushes customers, invoices, payments
    (OAuth2, idempotent upserts, audit log)"| QBO
    QBO -.-> |CDC poll + webhooks pull changes| GL
    TASK -.-> |Pushed on insert| GCAL
    GA4 -.-> |Marketing dashboard page| DESK[Desk Users]
    TRITON -.-> |"Click-to-call / SMS / voicemail /
    in-app AI assistant"| DESK
    PROJ & MRS & JI -.-> |"Read-only MCP tools + skills"| MCPT

    %% Style Definitions
    classDef crm fill:#E3F2FD,stroke:#2196F3,stroke-width:2px;
    classDef projects fill:#E0F7FA,stroke:#00BCD4,stroke-width:2px;
    classDef maintenance fill:#E0F2F1,stroke:#009688,stroke-width:2px;
    classDef kiosk fill:#FFF8E1,stroke:#FFC107,stroke-width:2px;
    classDef hr fill:#FFEBEE,stroke:#F44336,stroke-width:2px;
    classDef accounting fill:#ECEFF1,stroke:#607D8B,stroke-width:2px;
    classDef stock fill:#E8F5E9,stroke:#4CAF50,stroke-width:2px;
    classDef integration fill:#EDE7F6,stroke:#673AB7,stroke-width:2px,stroke-dasharray: 5 5;

    %% Class Assignments
    class LEAD,OPP,WON,SO crm;
    class PROJ,STEPS,CURR,RESP,TASK projects;
    class MC,PCON,MRD,MRS,SUP,PORTAL maintenance;
    class TECH,JI,TKL,MAP kiosk;
    class TT,ECL hr;
    class SINV,GL,TS accounting;
    class SE,WCL stock;
    class GDRIVE,QBO,TRITON,GCAL,GA4,MCPT integration;
""",
}


def sync_process_documents():
	"""``after_migrate`` entry point: upsert every chart defined above.

	Creates missing Process Documents and rewrites ``mermaid_code`` when the
	site copy differs from the canonical text (whitespace-insensitive at the
	ends). Site-created documents with other titles are not touched.
	"""
	for title, mermaid_code in PROCESS_DOCUMENTS.items():
		mermaid_code = mermaid_code.strip()
		if frappe.db.exists("Process Document", title):
			current = (frappe.db.get_value("Process Document", title, "mermaid_code") or "").strip()
			if current != mermaid_code:
				doc = frappe.get_doc("Process Document", title)
				doc.mermaid_code = mermaid_code
				doc.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{"doctype": "Process Document", "title": title, "mermaid_code": mermaid_code}
			).insert(ignore_permissions=True)

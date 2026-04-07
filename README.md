### ERPNext Enhancements

A collection of customizations and enhancements to ERPNext, focusing on project management, time tracking, and system integrations.

### Features

#### 1. Google Calendar Integration
Robust two-way synchronization between ERPNext and Google Calendar.
- **Supported DocTypes**: Events, Tasks, Projects, and ToDos.
- **Configuration**: Uses Frappe's Google Settings.
- **Capabilities**: Syncs start/end times, descriptions, and attendees. Handles deletions and updates in both directions.

#### 2. Time Kiosk
A simplified interface designed for tablets and mobile devices for employees to log time.
- **Clock In/Out**: Simple buttons to start and stop work intervals.
- **Project/Task Selection**: Filter active projects and tasks.
- **Geolocation**: Logs location coordinates when clocking in/out.
- **Timesheet Sync**: Automatically consolidates completed job intervals into standard ERPNext Timesheets.

#### 3. Project Management
Enhancements to the standard Project module:
- **Procurement Status**: Visual indicators for the status of linked Material Requests and Purchase Orders.
- **Project Merge**: Administrative utility to merge duplicate projects and move related documents.
- **Opportunity to Project**: Automatically carries over attachments and context when converting an Opportunity to a Project.
- **Validation**: Strict validation for project status transitions (e.g., "Canceled" spelling).

#### 4. Kanban Board Improvements
- **Touch Support**: Fixes drag-and-drop issues on touch devices by adjusting `Sortable.js` delay settings.
- **Horizontal Scrolling**: CSS fixes to ensure usable scrolling on wide boards with many columns.
- **WIP Limits**: (Experimental) Enforcement of Work-In-Progress limits on Kanban columns.

#### 5. Safety & usability
- **Safe Form Drafts**: Automatically saves unsaved form data to a temporary "User Form Draft" container. If a user navigates away or crashes, they can restore their work upon returning to the form.
- **Global Navigation Guard**: Warns users if they try to navigate away with unsaved changes.

#### 6. Integrations
- **Triton Bridge**: A background integration that syncs document updates to an external Triton service for vector embedding and AI indexing.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app erpnext_enhancements https://github.com/your-repo/erpnext_enhancements --branch develop
bench install-app erpnext_enhancements
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/erpnext_enhancements
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

## Google Analytics 4 Dashboard

This application includes a custom dashboard to view Google Analytics 4 (GA4) data directly within ERPNext.

### Installation & Setup Instructions

1. **Install the Custom App**
   If you haven't already installed `erpnext_enhancements`, run the following bench commands from your frappe-bench directory:

   ```bash
   cd $PATH_TO_YOUR_BENCH
   bench get-app erpnext_enhancements https://github.com/your-repo/erpnext_enhancements --branch develop
   bench install-app erpnext_enhancements
   ```
   *Note: Installing this app will automatically install the `google-analytics-data` Python package via `pyproject.toml` into your bench environment.*

2. **Create a Google Cloud Service Account**
   - Go to the [Google Cloud Console](https://console.cloud.google.com/).
   - Create a new project or select an existing one.
   - Navigate to **IAM & Admin** > **Service Accounts**.
   - Create a new Service Account and generate a new key (JSON format). Download this file securely.

2. **Grant Access in GA4**
   - Go to your [Google Analytics](https://analytics.google.com/) property.
   - Navigate to **Admin** > **Property Access Management**.
   - Add the email address of the Service Account you just created and assign it the **Viewer** role.

3. **Configure ERPNext**
   - In your ERPNext instance, search for **GA4 Settings** (this is a Single DocType).
   - In the **GA4 Property ID** field, enter your GA4 Property ID (found in GA4 Admin > Property Settings).
   - In the **Credentials JSON** field, attach the JSON key file you downloaded from Google Cloud. **Ensure you check the 'Is Private' checkbox** when uploading so it is placed securely in the private files directory.
   - Save the document.

4. **Access the Dashboard**
   - Once configured, you can access the dashboard by searching for **ga4-dashboard** in the ERPNext global search bar or navigating directly to `/app/ga4-dashboard`.

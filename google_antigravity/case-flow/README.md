# Case Flow

## Project Structure

- `frontend/`: Next.js frontend application.
- `backend/`: Django REST Framework backend application.

## Getting Started

### Prerequisites

- Python 3.13+
- Node.js
- `uv` (Python package manager)

### Backend Setup

1. **Install Dependencies**:
   The project uses `uv` for dependency management.

   ```bash
   uv sync
   ```

2. **Run Migrations** (already done, but good for reference):

   ```bash
   uv run python backend/manage.py migrate
   ```

3. **Start the Development Server**:
   ```bash
   uv run python backend/manage.py runserver
   ```
   The backend will start at `http://localhost:8000`.
   - Health Check: `http://localhost:8000/api/health/`
   - Admin: `http://localhost:8000/admin/`

### Frontend Setup

1. Navigate to the frontend directory:

   ```bash
   cd frontend
   ```

2. Install dependencies:

   ```bash
   npm install
   ```

3. Run the development server:
   ```bash
   npm run dev
   ```

# HOA SaaS Backend

## Prerequisites

- Python 3.12+
- PostgreSQL (or Neon DB as configured in `.env`)

## Setup

1.  **Navigate to the backend directory:**

    ```bash
    cd hoa-backend
    ```

2.  **Create a virtual environment (optional but recommended):**

    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```

3.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Database Migrations:**

    Ensure your `.env` file matches the database credentials. Then run:

    ```bash
    alembic upgrade head
    ```

## Running the Server

To start the development server:

```bash
uvicorn app.main:app --reload
```

The API will be available at [http://localhost:8000](http://localhost:8000).
API Documentation (Swagger UI) is at [http://localhost:8000/docs](http://localhost:8000/docs).

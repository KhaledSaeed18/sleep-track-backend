# 💤 SleepTrack API

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![JWT](https://img.shields.io/badge/JWT-000000?style=for-the-badge&logo=JSON%20web%20tokens)
![Tortoise ORM](https://img.shields.io/badge/Tortoise%20ORM-4B8BBE?style=for-the-badge&logo=tortoise&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-4051B5?style=for-the-badge&logo=uvicorn&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=for-the-badge&logo=pydantic&logoColor=white)
![Bcrypt](https://img.shields.io/badge/Bcrypt-4BC51D?style=for-the-badge&logo=auth0&logoColor=white)

A comprehensive backend API for sleep tracking and analytics, built with FastAPI and PostgreSQL.

## 🌟 Features

- **User Authentication**: JWT-based secure authentication system
- **Sleep Session Logging**: Track sleep start/end times with quality ratings
- **Sleep Analytics**:
  - Personal sleep statistics and trends
  - Weekly sleep summaries with consistency scoring
  - Timeline visualization for sleep patterns
- **User Management**: Profile management and secure password reset
- **Data Export**: RESTful API for integration with frontend applications

## 🛠️ Tech Stack

- **Backend Framework**: FastAPI
- **Database**: PostgreSQL with Tortoise ORM
- **Authentication**: JWT tokens with bcrypt password hashing
- **API Documentation**: Automatic OpenAPI/Swagger documentation
- **Database Migrations**: Aerich for schema management

## 📋 Prerequisites

- Python 3.8+
- PostgreSQL database
- pip package manager

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/KhaledSaeed18/sleep-track-backend.git
cd sleep-track-backend
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the root directory:

```env
# Database Configuration
DATABASE_URL=postgres://username:password@localhost:5432/sleeptrack

# JWT Configuration
SECRET_KEY=your-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Application Settings
ENVIRONMENT=development
DEBUG=true
```

### 4. Database Setup

Initialize the database with migrations:

```bash
aerich init -t app.config.TORTOISE_ORM
aerich init-db
```

### 5. Run the Application

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`

## 📚 API Documentation

Once the server is running, you can access:

- **Interactive API Documentation**: `http://localhost:8000/docs`
- **Alternative Documentation**: `http://localhost:8000/redoc`

## 🔐 API Endpoints

### Authentication

- `POST /auth/signup` - Register a new user
- `POST /auth/login` - User login and token generation

### Sleep Tracking

- `POST /sleep/` - Log a new sleep session
- `GET /sleep/` - Retrieve user's sleep sessions
- `GET /sleep/stats` - Get sleep statistics
- `GET /sleep/timeline` - Get sleep timeline data
- `GET /sleep/weekly-summary` - Get weekly sleep analytics
- `DELETE /sleep/{session_id}` - Delete a sleep session

### User Management

- `GET /user/me` - Get current user profile
- `PUT /user/profile` - Update user profile
- `POST /user/reset-password` - Reset user password
- `DELETE /user/account` - Delete user account

### Health Check

- `GET /` - API welcome message
- `GET /health` - Health check endpoint

## 🗄️ Database Schema

### Users Table

- User credentials and profile information
- Secure password hashing with bcrypt
- Email uniqueness validation

### Sleep Sessions Table

- Sleep start/end timestamps
- Quality ratings (1-5 scale)
- User association via foreign key

## 🔧 Configuration

The application uses environment-based configuration:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SECRET_KEY` | JWT signing secret | Required |
| `ALGORITHM` | JWT algorithm | HS256 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token expiration time | 30 |
| `ENVIRONMENT` | App environment | development |
| `DEBUG` | Debug mode | true |

## 🧪 Development

### Database Migrations

Create a new migration after model changes:

```bash
aerich migrate --name describe_your_changes
```

Apply migrations:

```bash
aerich upgrade
```

### Project Structure

```text
sleep-track/
├── app/
│   ├── auth/              # Authentication utilities
│   ├── models/            # Database models
│   ├── routes/            # API route handlers
│   ├── schemas/           # Pydantic schemas
│   ├── config.py          # Application configuration
│   └── main.py            # FastAPI application
├── migrations/            # Database migrations
├── requirements.txt       # Python dependencies
├── pyproject.toml         # Project configuration
└── README.md             # This file
```

## 🔒 Security Features

- **Password Security**: Bcrypt hashing with salt
- **JWT Authentication**: Secure token-based authentication
- **CORS Protection**: Configurable cross-origin request handling
- **Input Validation**: Pydantic schema validation
- **SQL Injection Protection**: ORM-based database queries

## 📊 Sleep Analytics Features

- **Personal Statistics**: Average sleep duration, quality ratings
- **Weekly Summaries**: Sleep consistency scoring and trends
- **Timeline Data**: Historical sleep pattern visualization
- **Quality Tracking**: Sleep quality trend analysis

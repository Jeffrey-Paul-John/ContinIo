USE continio;

CREATE TABLE IF NOT EXISTS telegram_teacher_links (
    teacher_id INT PRIMARY KEY,
    chat_id VARCHAR(30) NOT NULL UNIQUE,
    telegram_username VARCHAR(64) NULL,
    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_telegram_link_teacher FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_link_codes (
    teacher_id INT PRIMARY KEY,
    code_hash CHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_telegram_code_teacher FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_review_tokens (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id INT NOT NULL,
    teacher_id INT NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    confirm_code CHAR(6) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_telegram_review_session (session_id, teacher_id),
    CONSTRAINT fk_telegram_review_session FOREIGN KEY (session_id) REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_telegram_review_teacher FOREIGN KEY (teacher_id) REFERENCES teachers(id)
);

CREATE TABLE IF NOT EXISTS telegram_notifications (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id INT NOT NULL,
    notification_type ENUM('review','pending','final') NOT NULL,
    provider_message_id VARCHAR(80) NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_telegram_notification (session_id, notification_type),
    CONSTRAINT fk_telegram_notification_session FOREIGN KEY (session_id) REFERENCES attendance_sessions(id) ON DELETE CASCADE
);

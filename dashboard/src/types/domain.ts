export type DashboardSummary = {
  today_meetings: number;
  upcoming_meetings: number;
  due_tasks: number;
  overdue_tasks: number;
  pending_reminders: number;
  recent_messages: number;
  pending_email_approvals: number;
  failed_integrations: number;
  scheduler_health: string;
};

export type User = {
  id: string;
  name: string;
  email: string;
  role: string;
  status: string;
};

export type Person = {
  id: string;
  full_name: string;
  company?: string | null;
  job_title?: string | null;
  email?: string | null;
  whatsapp_number?: string | null;
  active: boolean;
};

export type Project = {
  id: string;
  person_id: string;
  person_name?: string | null;
  name: string;
  description?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Task = {
  id: string;
  title: string;
  description?: string | null;
  status: string;
  priority: string;
  assigned_person_id?: string | null;
  assigned_person_name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  due_date?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type Meeting = {
  id: string;
  title: string;
  status: string;
  start_time: string;
  timezone: string;
  preparation_status: string;
};

export type Reminder = {
  id: string;
  title: string;
  status: string;
  trigger_time: string;
  delivery_channel: string;
};

export type NotificationSettings = {
  task_change_email_notifications: boolean;
  task_change_email_recipients: string;
};

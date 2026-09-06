"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { api, TaskStatus, ApiError } from "./Api";

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 40;

interface UseTaskPollingResult {
  status: TaskStatus | null;
  isPolling: boolean;
  error: string | null;
  startPolling: (taskId: string) => void;
  stopPolling: () => void;
}

export function useTaskPolling(): UseTaskPollingResult {
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const attemptsRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>();

  // Cleanup when component unmounts
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const stopPolling = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = undefined;
    }

    setIsPolling(false);
  }, []);

  const poll = useCallback(
    async (taskId: string) => {
      try {
        const result = await api.tasks.get(taskId);

        setStatus(result);

        if (result.status === "SUCCESS" || result.status === "FAILURE") {
          stopPolling();
          return;
        }

        attemptsRef.current += 1;

        if (attemptsRef.current >= MAX_POLL_ATTEMPTS) {
          stopPolling();
          setError("Task is taking longer than expected. Please check back later.");
          return;
        }

        timeoutRef.current = setTimeout(() => {
          poll(taskId);
        }, POLL_INTERVAL_MS);
      } catch (err) {
        stopPolling();

        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to check task status"
        );
      }
    },
    [stopPolling]
  );

  const startPolling = useCallback(
    (taskId: string) => {
      // Cancel any existing polling session
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }

      attemptsRef.current = 0;
      setError(null);
      setStatus(null);
      setIsPolling(true);

      poll(taskId);
    },
    [poll]
  );

  return {
    status,
    isPolling,
    error,
    startPolling,
    stopPolling,
  };
}
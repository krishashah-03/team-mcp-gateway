import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (value: T | null) => void;
}

/** Load data on mount and whenever `deps` change; ignores responses from stale requests. */
export function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const latest = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(async () => {
    const id = ++latest.current;
    setLoading(true);
    try {
      const result = await fetcherRef.current();
      if (id !== latest.current) return;
      setData(result);
      setError(null);
    } catch (caught) {
      if (id !== latest.current) return;
      setError(caught instanceof ApiError ? caught.message : "Something went wrong");
    } finally {
      if (id === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}

export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong";
}

import { type Dispatch, type SetStateAction, useEffect, useState } from 'react';

export function useSessionState<T>(key: string, initialValue: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.sessionStorage.getItem(key);
      if (stored !== null) return JSON.parse(stored) as T;
    } catch {
      // 저장소를 사용할 수 없으면 일반 React 상태로 동작합니다.
    }
    return typeof initialValue === 'function' ? (initialValue as () => T)() : initialValue;
  });

  useEffect(() => {
    try {
      window.sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
      // 저장 실패가 화면 동작을 막지 않게 합니다.
    }
  }, [key, value]);

  return [value, setValue];
}

const root = "/api/v1";
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const hasFormData = options?.body instanceof FormData;
  const response = await fetch(root + path, {
    headers: {
      ...(hasFormData ? {} : { "Content-Type": "application/json" }),
      ...options?.headers,
    },
    ...options,
  });
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText);
  return response.json() as Promise<T>;
}

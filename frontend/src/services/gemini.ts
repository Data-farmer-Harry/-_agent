const apiKey = ''; // User placeholder
const GEMINI_MODEL = 'gemini-2.5-flash-preview-09-2025';

export async function callGemini(prompt: string, systemInstruction = ''): Promise<string> {
  if (!apiKey) {
    throw new Error('API Key is missing. Please configure GEMINI API KEY.');
  }
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${apiKey}`;
  
  const payload = {
    contents: [{ parts: [{ text: prompt }] }],
    systemInstruction: systemInstruction ? { parts: [{ text: systemInstruction }] } : undefined,
  };

  const fetchWithRetry = async (retries = 5, delay = 1000): Promise<any> => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      if (retries <= 0) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      return fetchWithRetry(retries - 1, delay * 2);
    }
  };

  const result = await fetchWithRetry();
  return result.candidates?.[0]?.content?.parts?.[0]?.text || '抱歉，AI 暂时无法生成回复。';
}

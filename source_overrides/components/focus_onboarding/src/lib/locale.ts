export const isRussianUiLocale = (locale: string): boolean => {
    const normalized = locale.toLowerCase();
    return normalized === 'ru' ||
        normalized.startsWith('ru-') ||
        normalized.startsWith('ru_');
};

// The welcome message's placeholders, filled the same way as
// shared/welcome_message.py's fill_welcome_message (LIVESTACK-44), for the
// live preview on the Settings page. tests/review_ui/test_welcome_message.py
// runs both on the same cases.
//
//   {nome}, {prova}  replaced by the value; with no value, the placeholder
//                    goes together with the one space and the comma right
//                    before it ("Olá, {nome}!" → "Olá!")
//   anything else    in braces stays as typed ("{Nome}", "{", "{}")

const PLACEHOLDER = /(,?)( ?)\{(nome|prova)\}/g;

export function fillWelcomeMessage(template, values) {
  return template.replace(PLACEHOLDER, (match, commaBefore, spaceBefore, name) => {
    const value = (values[name] ?? "").trim();
    if (!value) return "";
    return commaBefore + spaceBefore + value;
  });
}
